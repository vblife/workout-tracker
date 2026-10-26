"""Phase 4: period reports + progress series."""
import json
from datetime import datetime,date,timedelta,timezone
import core
import workouts,goals
from core import UserError,_local,_iso,_parse_time,to_unit,display_unit,get_settings,user_timezone,MI,FT
PERIODS=("day","week","month","quarter","year","all")
def period_bounds(period,anchor):
  d=anchor
  if period=="day": a=d; b=d+timedelta(days=1)
  elif period=="week": a=d-timedelta(days=d.weekday()); b=a+timedelta(days=7)
  elif period=="month": a=d.replace(day=1); b=(a+timedelta(days=32)).replace(day=1)
  elif period=="quarter": a=date(d.year,3*((d.month-1)//3)+1,1); b=(a+timedelta(days=95)).replace(day=1)
  elif period=="year": a=date(d.year,1,1); b=date(d.year+1,1,1)
  else: raise UserError("bad period")
  return a,b
def prev_bounds(period,a):
  return period_bounds(period,a-timedelta(days=1))
def label(period,a,b):
  if period=="day": return a.isoformat()
  if period=="week": return f"{a.isoformat()} – {(b-timedelta(days=1)).isoformat()}"
  if period=="month": return a.strftime("%Y-%m")
  if period=="quarter": return f"{a.year} Q{(a.month-1)//3+1}"
  if period=="year": return str(a.year)
  return "All time"
def U(d,tz): return _iso(datetime.combine(d,datetime.min.time(),tz))

def _all_sets(c):
  return c.execute("""SELECT x.*,e.canonical_name,e.name_zh,e.category,e.muscle_groups,e.tracking FROM sets x
    JOIN exercises e ON e.id=x.exercise_id JOIN sessions s ON s.id=x.session_id
    WHERE s.status='completed' ORDER BY x.performed_at,x.id""").fetchall()
def _pr_flags(rows):
  best={}; flags={}
  for r in rows:
    if r["is_warmup"] or not r["e1rm_kg"]: continue
    b=best.get(r["exercise_id"])
    if b is None or r["e1rm_kg"]>b+1e-9: flags[r["id"]]=b; best[r["exercise_id"]]=r["e1rm_kg"]
  return flags  # set id -> previous best (None = first ever)
def _summ(c,rows,sessions,unit,a=None,b=None,tz=None):
  tz=tz or user_timezone(c)
  imperial=unit=="lb"
  inr=lambda t:(a is None or t>=U(a,tz)) and (b is None or t<U(b,tz))
  R=[r for r in rows if inr(r["performed_at"]) and not r["is_warmup"]]; S=[s for s in sessions if inr(s["started_at"])]
  days={_local(s["started_at"],tz).date() for s in S}
  vol=sum((r["weight_kg"] or 0)*(r["reps"] or 0) for r in R)
  cardio_min=sum(s["duration_min"] or 0 for s in S if s["type"] in("cardio","hike","hiit"))+sum((r["duration_s"] or 0)/60 for r in R if r["category"] in("cardio","hike"))
  km=sum(s["distance_km"] or 0 for s in S)+sum((r["distance_m"] or 0)/1000 for r in R if r["category"] in("cardio","hike"))
  hk=[s for s in S if s["type"]=="hike"]
  musc={}
  for r in R:
    for m in json.loads(r["muscle_groups"] or "[]"): musc[m]=musc.get(m,0)+1
  by_ex={}
  for r in R: by_ex[r["canonical_name"]]=by_ex.get(r["canonical_name"],0)+1
  types={}
  for s in S: types[s["type"] or "other"]=types.get(s["type"] or "other",0)+1
  dist=lambda k:round(k/MI,1) if imperial else round(k,1)
  training_min=sum(s["duration_min"] or s["calorie_duration_min"] or 0 for s in S)
  calories=sum(s["estimated_calories"] or 0 for s in S)
  return {"days_trained":len(days),"sessions":len(S),"sessions_by_type":types,"sets":len(R),"reps":sum(r["reps"] or 0 for r in R),
    "training_minutes":round(training_min),"estimated_calories":round(calories),
    "volume":to_unit(vol,unit),"cardio_minutes":round(cardio_min),"distance":dist(km),
    "hikes":len(hk),"hike_distance":dist(sum(s["distance_km"] or 0 for s in hk)),
    "hike_elevation":round(sum(s["elevation_gain_m"] or 0 for s in hk)/(FT if imperial else 1)),
    "hiit_sessions":types.get("hiit",0),"top_exercises":sorted(by_ex.items(),key=lambda kv:-kv[1])[:8],
    "sets_by_muscle":dict(sorted(musc.items(),key=lambda kv:-kv[1])),"_days":days}
def _delta(cur,prev):
  out={}
  for k in("days_trained","sessions","sets","volume","cardio_minutes","distance"):
    p,q=prev.get(k) or 0,cur.get(k) or 0
    out[k]={"prev":p,"change":round(q-p,1),"pct":None if not p else round(100*(q-p)/p)}
  return out
def _weeks(days,a,b,target):
  wk={}
  d=a-timedelta(days=a.weekday())
  while d<b: wk[d]=0; d+=timedelta(days=7)
  for x in days: k=x-timedelta(days=x.weekday()); wk[k]=wk.get(k,0)+1
  vals=[(k.isoformat(),v) for k,v in sorted(wk.items())]
  met=[v>=target for _,v in vals]; streak=best=0
  for m in met: streak=streak+1 if m else 0; best=max(best,streak)
  return vals,sum(met),best
def _longest_day_streak(days):
  best=cur=0; prev=None
  for d in sorted(days): cur=cur+1 if prev and (d-prev).days==1 else 1; best=max(best,cur); prev=d
  return best
def _lift_progress(rows,unit,a,b,tz,n=6):
  inr=lambda t:(a is None or t>=U(a,tz)) and t<U(b,tz)
  per={}
  for r in rows:
    if r["is_warmup"] or not r["e1rm_kg"] or not inr(r["performed_at"]): continue
    per.setdefault(r["canonical_name"],[]).append(r)
  out=[]
  for name,rs in sorted(per.items(),key=lambda kv:-len(kv[1]))[:n]:
    first_day=_local(rs[0]["performed_at"],tz).date(); fs=[r for r in rs if _local(r["performed_at"],tz).date()==first_day]
    last_day=_local(rs[-1]["performed_at"],tz).date(); ls=[r for r in rs if _local(r["performed_at"],tz).date()==last_day]
    e0=max(r["e1rm_kg"] for r in fs); e1=max(r["e1rm_kg"] for r in ls); best=max(rs,key=lambda r:r["e1rm_kg"])
    # plateau: no e1RM improvement over the last 4 weeks of data vs before
    cut=_iso(_parse_time(rs[-1]["performed_at"],tz)-timedelta(days=28))
    before=[r["e1rm_kg"] for r in rs if r["performed_at"]<cut]; recent=[r["e1rm_kg"] for r in rs if r["performed_at"]>=cut]
    plateau=bool(before and recent and max(recent)<=max(before)+1e-9)
    out.append({"exercise":name,"sets":len(rs),"e1rm_start":to_unit(e0,unit),"e1rm_end":to_unit(e1,unit),"e1rm_change_pct":round(100*(e1-e0)/e0,1) if e0 else None,
      "best_set":{"weight":to_unit(best["weight_kg"],unit),"reps":best["reps"],"e1rm":to_unit(best["e1rm_kg"],unit),"date":_local(best["performed_at"],tz).date().isoformat()},"plateau_4w":plateau})
  return out
def _month_shift(d,delta):
  index=d.year*12+d.month-1+delta
  return date(index//12,index%12+1,1)

def _trend_bounds(period,anchor):
  if period=="day":
    return "day",[(anchor-timedelta(days=i),anchor-timedelta(days=i)+timedelta(days=1)) for i in range(27,-1,-1)]
  if period=="week":
    current=anchor-timedelta(days=anchor.weekday())
    return "week",[(current-timedelta(weeks=i),current-timedelta(weeks=i-1)) for i in range(5,-1,-1)]
  if period=="month":
    current=anchor.replace(day=1)
    return "month",[(_month_shift(current,-i),_month_shift(current,-i+1)) for i in range(5,-1,-1)]
  if period=="year":
    return "month",[(date(anchor.year,m,1),date(anchor.year+1,1,1) if m==12 else date(anchor.year,m+1,1)) for m in range(1,13)]
  a,b=period_bounds(period,anchor) if period!="all" else (anchor-timedelta(days=365),anchor+timedelta(days=1))
  return ("week" if period=="quarter" else "month"),[(a,b)]

def _daily_intake(c,a,b):
  """Confirmed calories_kcal per user-local date in [a,b): {iso_date: kcal}."""
  return {r["local_date"]:r["kcal"] for r in c.execute("""SELECT local_date,sum(calories_kcal) kcal FROM nutrition_entries
    WHERE local_date>=? AND local_date<? AND calories_kcal IS NOT NULL GROUP BY local_date""",(a.isoformat(),b.isoformat()))}

def _deficit_totals(intake,maintenance,a,b):
  """Energy deficit (maintenance - intake) summed over days in [a,b) that have intake. None without maintenance."""
  kcal=[v for k,v in intake.items() if a.isoformat()<=k<b.isoformat()]
  deficits=[maintenance-v for v in kcal] if maintenance is not None else []
  return {"days_with_intake":len(kcal),
          "total_energy_deficit_kcal":round(sum(deficits),1) if deficits else None,
          "avg_daily_energy_deficit_kcal":round(sum(deficits)/len(deficits),1) if deficits else None}

def _trend(c,period,anchor,unit,tz,maintenance=None):
  """Trend points. Multi-day periods add training_days (the primary training trend) and the energy deficit per point;
  week also adds day_points (one per day of the report week). Day keeps its 28-day rhythm without training-day fields.
  training_minutes stays on each point for backward compatibility only."""
  group,bounds=_trend_bounds(period,anchor); points=[]; multi=period!="day"
  intake=_daily_intake(c,bounds[0][0],bounds[-1][1]) if multi else {}
  for a,b in bounds:
    sessions=c.execute("SELECT * FROM sessions WHERE status='completed' AND started_at>=? AND started_at<?",(U(a,tz),U(b,tz))).fetchall()
    volume=c.execute("""SELECT coalesce(sum(x.weight_kg*x.reps),0) FROM sets x JOIN sessions s ON s.id=x.session_id
      WHERE s.status='completed' AND x.is_warmup=0 AND x.performed_at>=? AND x.performed_at<?""",(U(a,tz),U(b,tz))).fetchone()[0]
    training=sum(x["duration_min"] or x["calorie_duration_min"] or 0 for x in sessions)
    cardio=sum(x["duration_min"] or 0 for x in sessions if x["type"] in ("cardio","hike","hiit"))
    point={"period":a.isoformat() if group!="month" else a.strftime("%Y-%m"),"training_minutes":round(training,1),
      "strength_volume":to_unit(volume,unit),"cardio_minutes":round(cardio,1)}
    if multi:
      point["training_days"]=len({_local(x["started_at"],tz).date() for x in sessions})
      point.update(_deficit_totals(intake,maintenance,a,b))
    points.append(point)
  out={"group_by":group,"points":points}
  if multi: out["primary_training_metric"]="training_days"
  if period=="week":
    a,b=period_bounds("week",anchor); intake_w={k:v for k,v in intake.items() if a.isoformat()<=k<b.isoformat()}
    trained={}
    for x in c.execute("SELECT started_at FROM sessions WHERE status='completed' AND started_at>=? AND started_at<?",(U(a,tz),U(b,tz))):
      d=_local(x["started_at"],tz).date(); trained[d]=trained.get(d,0)+1
    out["day_points"]=[]
    for i in range(7):
      d=a+timedelta(days=i); kcal=intake_w.get(d.isoformat())
      out["day_points"].append({"date":d.isoformat(),"trained":1 if trained.get(d) else 0,"sessions":trained.get(d,0),
        "intake_kcal":round(kcal,1) if kcal is not None else None,
        "energy_deficit_kcal":round(maintenance-kcal,1) if kcal is not None and maintenance is not None else None})
  return out

def _nutrition(c,period,a,b,tz):
  # Group by the entry's stored local_date, the same day key as nutrition_daily_totals and nutrition goals.
  rows=c.execute("SELECT * FROM nutrition_entries WHERE local_date>=? AND local_date<? ORDER BY consumed_at",(a.isoformat(),b.isoformat())).fetchall()
  local_day=lambda r:r["local_date"]
  out={"entries":len(rows),"days_with_entries":len({local_day(r) for r in rows})}
  for field in ("calories_kcal","protein_g","carbohydrates_g","fat_g","fiber_g","saturated_fat_g","sugar_g","sodium_mg"):
    values=[r[field] for r in rows if r[field] is not None]
    known_days={local_day(r) for r in rows if r[field] is not None}
    out[field]=round(sum(values),1) if values else None
    if period!="day": out[field+"_daily_average"]=round(sum(values)/len(known_days),1) if values and known_days else None
  out["confidence"]={level:sum(1 for r in rows if r["confidence"]==level) for level in ("higher","moderate","rough")}
  return out

def _calorie_max_goal(c):
  """The user's active daily calories_kcal maximum goal, or None. Never a hard-coded number."""
  for g in goals.list_goals(c,domain="nutrition")["goals"]:
    if g["metric"]=="calories_kcal" and g["period"]=="day" and g["semantics"]=="maximum" and g["target_value"] is not None: return g
  return None

def _energy_balance(c,period,a,b,settings):
  """Energy deficit vs settings.maintenance_kcal (deficit = maintenance - confirmed intake).
  Workout-burned calories are reported separately and never folded in here."""
  maintenance=settings.get("maintenance_kcal"); goal=_calorie_max_goal(c)
  cal_max=goal["target_value"] if goal else None
  target=round(maintenance-cal_max) if maintenance is not None and cal_max is not None else None
  rows=c.execute("""SELECT local_date,sum(calories_kcal) kcal FROM nutrition_entries WHERE local_date>=? AND local_date<?
    AND calories_kcal IS NOT NULL GROUP BY local_date ORDER BY local_date""",(a.isoformat(),b.isoformat())).fetchall()
  days=[{"date":r["local_date"],"intake_kcal":round(r["kcal"],1),
         "energy_deficit_kcal":round(maintenance-r["kcal"],1) if maintenance is not None else None,
         "within_calorie_max":(r["kcal"]<=cal_max) if cal_max is not None else None,
         "met_deficit_target":(maintenance-r["kcal"]>=target) if target is not None else None} for r in rows]
  out={"maintenance_kcal":maintenance,"maintenance_source":"settings" if maintenance is not None else None,
       "calorie_max_kcal":cal_max,"calorie_max_goal_id":goal["goal_id"] if goal else None,"target_energy_deficit_kcal":target,
       "available":maintenance is not None and bool(days)}
  if period=="day":
    d=days[0] if days else None
    out.update(intake_kcal=d["intake_kcal"] if d else None,energy_deficit_kcal=d["energy_deficit_kcal"] if d else None,
               within_calorie_max=d["within_calorie_max"] if d else None,met_deficit_target=d["met_deficit_target"] if d else None,
               kcal_under_max=round(cal_max-d["intake_kcal"],1) if d and cal_max is not None else None,
               deficit_vs_target_kcal=round(d["energy_deficit_kcal"]-target,1) if d and target is not None and d["energy_deficit_kcal"] is not None else None)
    return out
  deficits=[d["energy_deficit_kcal"] for d in days if d["energy_deficit_kcal"] is not None]
  out.update(days_with_intake=len(days),
             total_energy_deficit_kcal=round(sum(deficits),1) if deficits else None,
             avg_daily_energy_deficit_kcal=round(sum(deficits)/len(deficits),1) if deficits else None,
             days_meeting_deficit_target=sum(1 for d in days if d["met_deficit_target"]) if target is not None else None,
             days_within_calorie_max=sum(1 for d in days if d["within_calorie_max"]) if cal_max is not None else None)
  if period in ("week","month"): out["days"]=days
  return out

def report(c,period="week",anchor_date=None,now=None):
  if period not in PERIODS: raise UserError("period must be day|week|month|quarter|year|all")
  unit=display_unit(c); s=get_settings(c); tzs=core.timezone_status(c,s); tz=user_timezone(c,s); target=s.get("days_per_week") or 4
  now=now or datetime.now(timezone.utc); anchor=date.fromisoformat(anchor_date) if anchor_date else now.astimezone(tz).date()
  workouts.reconcile_active(c,now)
  rows=_all_sets(c); sessions=c.execute("SELECT * FROM sessions WHERE status='completed' ORDER BY started_at").fetchall()
  if period=="all":
    first=min([_local(x["started_at"],tz).date() for x in sessions],default=anchor)
    a,b=first,anchor+timedelta(days=1); pa=pb=None
  else: a,b=period_bounds(period,anchor); pa,pb=prev_bounds(period,a)
  cur=_summ(c,rows,sessions,unit,a,b,tz); days=cur.pop("_days")
  out={"period":period,"label":label(period,a,b),"start":a.isoformat(),"end_exclusive":b.isoformat(),"unit":unit,
       "distance_unit":"mi" if unit=="lb" else "km","elevation_unit":"ft" if unit=="lb" else "m","target_days_per_week":target,**tzs,"summary":cur}
  out["active_workout"]=workouts.get_active(c,now)["active_workout"]
  out["nutrition"]=_nutrition(c,period,a,b,tz)
  out["energy_balance"]=_energy_balance(c,period,a,b,s)
  out["trend_series"]=_trend(c,period,anchor,unit,tz,s.get("maintenance_kcal"))
  out["goal_progress"]=[]
  for goal in goals.list_goals(c)["goals"]:
    try: out["goal_progress"].append(goals.goal_progress(c,goal["goal_id"],anchor.isoformat()))
    except UserError: pass
  out["routine_usage"]=[{"routine_id":r["source_routine_id"],"name":r["name"],"sessions":r["n"]} for r in c.execute("""SELECT s.source_routine_id,r.name,count(*) n FROM sessions s JOIN workout_routines r ON r.id=s.source_routine_id
    WHERE s.status='completed' AND s.started_at>=? AND s.started_at<? GROUP BY s.source_routine_id,r.name ORDER BY n DESC""",(U(a,tz),U(b,tz)))]
  coverage=c.execute("""SELECT count(*) total,sum(CASE WHEN estimated_calories IS NOT NULL THEN 1 ELSE 0 END) estimated,
    sum(CASE WHEN calorie_confidence='rough' THEN 1 ELSE 0 END) rough FROM sessions WHERE status='completed' AND started_at>=? AND started_at<?""",(U(a,tz),U(b,tz))).fetchone()
  out["calorie_confidence_coverage"]={"sessions":coverage["total"],"estimated":coverage["estimated"] or 0,"rough":coverage["rough"] or 0}
  if pa:
    prev=_summ(c,rows,sessions,unit,pa,pb,tz); prev.pop("_days"); out["vs_previous"]={"label":label(period,pa,pb),**_delta(cur,prev)}
  first_any=min([_local(x["started_at"],tz).date() for x in sessions],default=a)
  wk,met,streak=_weeks(days,max(a,first_any),min(b,anchor+timedelta(days=1)),target)  # don't count weeks before tracking began
  out["adherence"]={"weeks":len(wk),"weeks_meeting_target":met,"longest_week_streak":streak,"longest_day_streak":_longest_day_streak(days),
                    "avg_days_per_week":round(len(days)/max(1,len(wk)),1)}
  flags=_pr_flags(rows); inr=lambda t:t>=U(a,tz) and t<U(b,tz)
  prs=[{"exercise":r["canonical_name"],"date":_local(r["performed_at"],tz).date().isoformat(),"weight":to_unit(r["weight_kg"],unit),"reps":r["reps"],
        "e1rm":to_unit(r["e1rm_kg"],unit),"previous_best_e1rm":to_unit(flags[r["id"]],unit)} for r in rows if r["id"] in flags and inr(r["performed_at"])]
  # keep only the final PR per exercise per day to avoid noise
  ded={}
  for p in prs: ded[(p["exercise"],p["date"])]=p
  out["prs"]=list(ded.values())[-20:]
  out["lifts"]=_lift_progress(rows,unit,a,b,tz)
  # chart series
  gb="day" if period=="week" else "week" if period in("month","quarter") else "month"
  ser={}
  for r in rows:
    if r["is_warmup"] or not inr(r["performed_at"]): continue
    k=_bucket(_local(r["performed_at"],tz),gb); d=ser.setdefault(k,{"period":k,"sets":0,"volume":0.0,"cardio_minutes":0.0,"days":set()})
    d["sets"]+=1; d["volume"]+=(r["weight_kg"] or 0)*(r["reps"] or 0); d["days"].add(_local(r["performed_at"],tz).date())
  for x in sessions:
    if not inr(x["started_at"]): continue
    k=_bucket(_local(x["started_at"],tz),gb); d=ser.setdefault(k,{"period":k,"sets":0,"volume":0.0,"cardio_minutes":0.0,"days":set()})
    d["days"].add(_local(x["started_at"],tz).date())
    if x["type"] in("cardio","hike","hiit"): d["cardio_minutes"]+=x["duration_min"] or 0
  out["series"]={"group_by":gb,"points":[{"period":v["period"],"sets":v["sets"],"volume":to_unit(v["volume"],unit),"cardio_minutes":round(v["cardio_minutes"]),"days":len(v["days"])} for _,v in sorted(ser.items())]}
  if period=="all":
    tot_sessions=len(sessions); ms=[m for m in(10,25,50,100,200,365,500,1000) if tot_sessions>=m]
    out["milestones"]={"sessions_total":tot_sessions,"reached":ms,"next":next((m for m in(10,25,50,100,200,365,500,1000) if m>tot_sessions),None)}
  out["highlights"]=_highlights(out)
  return out
from core import _bucket
def _highlights(r):
  h=[]; s=r["summary"]
  if r["period"]!="all": h.append(f"{s['days_trained']} training days (target {r['target_days_per_week']}/week).")
  if r["prs"]: h.append(f"{len(r['prs'])} PR(s): "+", ".join(f"{p['exercise']} {p['weight']}×{p['reps']}" for p in r["prs"][-3:]))
  if s["hikes"]: h.append(f"{s['hikes']} hike(s), {s['hike_distance']} {r['distance_unit']}, +{s['hike_elevation']} {r['elevation_unit']}.")
  pl=[l["exercise"] for l in r["lifts"] if l["plateau_4w"]]
  if pl and r["period"] in("quarter","year","all"): h.append("Plateau (4w no e1RM gain): "+", ".join(pl))
  return h

RANGES={"4w":28,"12w":84,"6m":183,"1y":365}
def show_progress(c,exercise=None,metric=None,range="12w",now=None):
  now=now or datetime.now(timezone.utc); unit=display_unit(c); tz=user_timezone(c)
  workouts.reconcile_active(c,now)
  if range not in(*RANGES,"all"): raise UserError("range must be 4w|12w|6m|1y|all")
  frm=None if range=="all" else (now.astimezone(tz).date()-timedelta(days=RANGES[range])).isoformat()
  gb="day" if range=="4w" else "week" if range in("12w","6m") else "month"
  out={"range":range,"unit":unit,"group_by":gb}
  if exercise:
    m=metric or "e1rm"; q=core.query_stats(c,m,exercise,from_date=frm,group_by=gb)
    out.update(kind="exercise",exercise=q["exercise"],metric=m,points=[{"period":p["period"],"value":p.get(m) if m!="best_set" else (p["best_set"] or {}).get("e1rm"),"sets":p["sets"]} for p in q["series"]])
    vals=[p["value"] for p in out["points"] if p["value"] is not None]
    out["stats"]={"first":vals[0] if vals else None,"last":vals[-1] if vals else None,"best":max(vals) if vals else None,
                  "change_pct":round(100*(vals[-1]-vals[0])/vals[0],1) if len(vals)>1 and vals[0] else None}
    rows=_all_sets(c); flags=_pr_flags(rows)
    out["prs"]=[{"date":_local(r["performed_at"],tz).date().isoformat(),"weight":to_unit(r["weight_kg"],unit),"reps":r["reps"],"e1rm":to_unit(r["e1rm_kg"],unit)}
                for r in rows if r["id"] in flags and r["canonical_name"]==q["exercise"] and (frm is None or _local(r["performed_at"],tz).date().isoformat()>=frm)][-10:]
  else:
    m=metric or "volume"
    if m not in("volume","sets","days","cardio_minutes"): raise UserError("overall metric must be volume|sets|days|cardio_minutes")
    a=date.fromisoformat(frm) if frm else None; b=now.astimezone(tz).date()+timedelta(days=1)
    rows=_all_sets(c); sessions=c.execute("SELECT * FROM sessions").fetchall(); ser={}
    def k_(t): return _bucket(_local(t,tz),gb)
    ok=lambda t: (a is None or t>=U(a,tz)) and t<U(b,tz)
    for r in rows:
      if r["is_warmup"] or not ok(r["performed_at"]): continue
      d=ser.setdefault(k_(r["performed_at"]),{"sets":0,"volume":0.0,"days":set(),"cardio_minutes":0.0}); d["sets"]+=1; d["volume"]+=(r["weight_kg"] or 0)*(r["reps"] or 0)
    for x in sessions:
      if not ok(x["started_at"]): continue
      d=ser.setdefault(k_(x["started_at"]),{"sets":0,"volume":0.0,"days":set(),"cardio_minutes":0.0}); d["days"].add(_local(x["started_at"],tz).date())
      if x["type"] in("cardio","hike","hiit"): d["cardio_minutes"]+=x["duration_min"] or 0
    conv=lambda d:{"volume":to_unit(d["volume"],unit),"sets":d["sets"],"days":len(d["days"]),"cardio_minutes":round(d["cardio_minutes"])}[m]
    out.update(kind="overall",exercise=None,metric=m,points=[{"period":k,"value":conv(v)} for k,v in sorted(ser.items())])
    vals=[p["value"] for p in out["points"]]; out["stats"]={"total":round(sum(vals),1) if vals else 0,"best":max(vals) if vals else None,"periods":len(vals)}
  return out
