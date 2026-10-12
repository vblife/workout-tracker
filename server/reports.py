"""Phase 4: period reports + progress series."""
import json
from datetime import datetime,date,timedelta,timezone
import core
from core import UserError,TZ,_local,_iso,_parse_time,to_unit,display_unit,get_settings,MI,FT
PERIODS=("week","month","quarter","year","all")
def period_bounds(period,anchor):
  d=anchor
  if period=="week": a=d-timedelta(days=d.weekday()); b=a+timedelta(days=7)
  elif period=="month": a=d.replace(day=1); b=(a+timedelta(days=32)).replace(day=1)
  elif period=="quarter": a=date(d.year,3*((d.month-1)//3)+1,1); b=(a+timedelta(days=95)).replace(day=1)
  elif period=="year": a=date(d.year,1,1); b=date(d.year+1,1,1)
  else: raise UserError("bad period")
  return a,b
def prev_bounds(period,a):
  return period_bounds(period,a-timedelta(days=1))
def label(period,a,b):
  if period=="week": return f"{a.isoformat()} – {(b-timedelta(days=1)).isoformat()}"
  if period=="month": return a.strftime("%Y-%m")
  if period=="quarter": return f"{a.year} Q{(a.month-1)//3+1}"
  if period=="year": return str(a.year)
  return "All time"
def U(d): return _iso(datetime.combine(d,datetime.min.time(),TZ))

def _all_sets(c):
  return c.execute("""SELECT x.*,e.canonical_name,e.name_zh,e.category,e.muscle_groups,e.tracking FROM sets x
    JOIN exercises e ON e.id=x.exercise_id ORDER BY x.performed_at,x.id""").fetchall()
def _pr_flags(rows):
  best={}; flags={}
  for r in rows:
    if r["is_warmup"] or not r["e1rm_kg"]: continue
    b=best.get(r["exercise_id"])
    if b is None or r["e1rm_kg"]>b+1e-9: flags[r["id"]]=b; best[r["exercise_id"]]=r["e1rm_kg"]
  return flags  # set id -> previous best (None = first ever)
def _summ(c,rows,sessions,unit,a=None,b=None):
  imperial=unit=="lb"
  inr=lambda t:(a is None or t>=U(a)) and (b is None or t<U(b))
  R=[r for r in rows if inr(r["performed_at"]) and not r["is_warmup"]]; S=[s for s in sessions if inr(s["started_at"])]
  days={_local(s["started_at"]).date() for s in S}
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
  return {"days_trained":len(days),"sessions":len(S),"sessions_by_type":types,"sets":len(R),"reps":sum(r["reps"] or 0 for r in R),
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
def _lift_progress(rows,unit,a,b,n=6):
  inr=lambda t:(a is None or t>=U(a)) and t<U(b)
  per={}
  for r in rows:
    if r["is_warmup"] or not r["e1rm_kg"] or not inr(r["performed_at"]): continue
    per.setdefault(r["canonical_name"],[]).append(r)
  out=[]
  for name,rs in sorted(per.items(),key=lambda kv:-len(kv[1]))[:n]:
    first_day=_local(rs[0]["performed_at"]).date(); fs=[r for r in rs if _local(r["performed_at"]).date()==first_day]
    last_day=_local(rs[-1]["performed_at"]).date(); ls=[r for r in rs if _local(r["performed_at"]).date()==last_day]
    e0=max(r["e1rm_kg"] for r in fs); e1=max(r["e1rm_kg"] for r in ls); best=max(rs,key=lambda r:r["e1rm_kg"])
    # plateau: no e1RM improvement over the last 4 weeks of data vs before
    cut=_iso(_parse_time(rs[-1]["performed_at"])-timedelta(days=28))
    before=[r["e1rm_kg"] for r in rs if r["performed_at"]<cut]; recent=[r["e1rm_kg"] for r in rs if r["performed_at"]>=cut]
    plateau=bool(before and recent and max(recent)<=max(before)+1e-9)
    out.append({"exercise":name,"sets":len(rs),"e1rm_start":to_unit(e0,unit),"e1rm_end":to_unit(e1,unit),"e1rm_change_pct":round(100*(e1-e0)/e0,1) if e0 else None,
      "best_set":{"weight":to_unit(best["weight_kg"],unit),"reps":best["reps"],"e1rm":to_unit(best["e1rm_kg"],unit),"date":_local(best["performed_at"]).date().isoformat()},"plateau_4w":plateau})
  return out
def report(c,period="week",anchor_date=None,now=None):
  if period not in PERIODS: raise UserError("period must be week|month|quarter|year|all")
  unit=display_unit(c); s=get_settings(c); target=s.get("days_per_week") or 4
  now=now or datetime.now(timezone.utc); anchor=date.fromisoformat(anchor_date) if anchor_date else now.astimezone(TZ).date()
  rows=_all_sets(c); sessions=c.execute("SELECT * FROM sessions ORDER BY started_at").fetchall()
  if period=="all":
    first=min([_local(x["started_at"]).date() for x in sessions],default=anchor)
    a,b=first,anchor+timedelta(days=1); pa=pb=None
  else: a,b=period_bounds(period,anchor); pa,pb=prev_bounds(period,a)
  cur=_summ(c,rows,sessions,unit,a,b); days=cur.pop("_days")
  out={"period":period,"label":label(period,a,b),"start":a.isoformat(),"end_exclusive":b.isoformat(),"unit":unit,
       "distance_unit":"mi" if unit=="lb" else "km","elevation_unit":"ft" if unit=="lb" else "m","target_days_per_week":target,"summary":cur}
  if pa:
    prev=_summ(c,rows,sessions,unit,pa,pb); prev.pop("_days"); out["vs_previous"]={"label":label(period,pa,pb),**_delta(cur,prev)}
  first_any=min([_local(x["started_at"]).date() for x in sessions],default=a)
  wk,met,streak=_weeks(days,max(a,first_any),min(b,anchor+timedelta(days=1)),target)  # don't count weeks before tracking began
  out["adherence"]={"weeks":len(wk),"weeks_meeting_target":met,"longest_week_streak":streak,"longest_day_streak":_longest_day_streak(days),
                    "avg_days_per_week":round(len(days)/max(1,len(wk)),1)}
  flags=_pr_flags(rows); inr=lambda t:t>=U(a) and t<U(b)
  prs=[{"exercise":r["canonical_name"],"date":_local(r["performed_at"]).date().isoformat(),"weight":to_unit(r["weight_kg"],unit),"reps":r["reps"],
        "e1rm":to_unit(r["e1rm_kg"],unit),"previous_best_e1rm":to_unit(flags[r["id"]],unit)} for r in rows if r["id"] in flags and inr(r["performed_at"])]
  # keep only the final PR per exercise per day to avoid noise
  ded={}
  for p in prs: ded[(p["exercise"],p["date"])]=p
  out["prs"]=list(ded.values())[-20:]
  out["lifts"]=_lift_progress(rows,unit,a,b)
  # chart series
  gb="day" if period=="week" else "week" if period in("month","quarter") else "month"
  ser={}
  for r in rows:
    if r["is_warmup"] or not inr(r["performed_at"]): continue
    k=_bucket(_local(r["performed_at"]),gb); d=ser.setdefault(k,{"period":k,"sets":0,"volume":0.0,"cardio_minutes":0.0,"days":set()})
    d["sets"]+=1; d["volume"]+=(r["weight_kg"] or 0)*(r["reps"] or 0); d["days"].add(_local(r["performed_at"]).date())
  for x in sessions:
    if not inr(x["started_at"]): continue
    k=_bucket(_local(x["started_at"]),gb); d=ser.setdefault(k,{"period":k,"sets":0,"volume":0.0,"cardio_minutes":0.0,"days":set()})
    d["days"].add(_local(x["started_at"]).date())
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
  now=now or datetime.now(timezone.utc); unit=display_unit(c)
  if range not in(*RANGES,"all"): raise UserError("range must be 4w|12w|6m|1y|all")
  frm=None if range=="all" else (now.astimezone(TZ).date()-timedelta(days=RANGES[range])).isoformat()
  gb="day" if range=="4w" else "week" if range in("12w","6m") else "month"
  out={"range":range,"unit":unit,"group_by":gb}
  if exercise:
    m=metric or "e1rm"; q=core.query_stats(c,m,exercise,from_date=frm,group_by=gb)
    out.update(kind="exercise",exercise=q["exercise"],metric=m,points=[{"period":p["period"],"value":p.get(m) if m!="best_set" else (p["best_set"] or {}).get("e1rm"),"sets":p["sets"]} for p in q["series"]])
    vals=[p["value"] for p in out["points"] if p["value"] is not None]
    out["stats"]={"first":vals[0] if vals else None,"last":vals[-1] if vals else None,"best":max(vals) if vals else None,
                  "change_pct":round(100*(vals[-1]-vals[0])/vals[0],1) if len(vals)>1 and vals[0] else None}
    rows=_all_sets(c); flags=_pr_flags(rows)
    out["prs"]=[{"date":_local(r["performed_at"]).date().isoformat(),"weight":to_unit(r["weight_kg"],unit),"reps":r["reps"],"e1rm":to_unit(r["e1rm_kg"],unit)}
                for r in rows if r["id"] in flags and r["canonical_name"]==q["exercise"] and (frm is None or _local(r["performed_at"]).date().isoformat()>=frm)][-10:]
  else:
    m=metric or "volume"
    if m not in("volume","sets","days","cardio_minutes"): raise UserError("overall metric must be volume|sets|days|cardio_minutes")
    a=date.fromisoformat(frm) if frm else None; b=now.astimezone(TZ).date()+timedelta(days=1)
    rows=_all_sets(c); sessions=c.execute("SELECT * FROM sessions").fetchall(); ser={}
    def k_(t): return _bucket(_local(t),gb)
    ok=lambda t: (a is None or t>=U(a)) and t<U(b)
    for r in rows:
      if r["is_warmup"] or not ok(r["performed_at"]): continue
      d=ser.setdefault(k_(r["performed_at"]),{"sets":0,"volume":0.0,"days":set(),"cardio_minutes":0.0}); d["sets"]+=1; d["volume"]+=(r["weight_kg"] or 0)*(r["reps"] or 0)
    for x in sessions:
      if not ok(x["started_at"]): continue
      d=ser.setdefault(k_(x["started_at"]),{"sets":0,"volume":0.0,"days":set(),"cardio_minutes":0.0}); d["days"].add(_local(x["started_at"]).date())
      if x["type"] in("cardio","hike","hiit"): d["cardio_minutes"]+=x["duration_min"] or 0
    conv=lambda d:{"volume":to_unit(d["volume"],unit),"sets":d["sets"],"days":len(d["days"]),"cardio_minutes":round(d["cardio_minutes"])}[m]
    out.update(kind="overall",exercise=None,metric=m,points=[{"period":k,"value":conv(v)} for k,v in sorted(ser.items())])
    vals=[p["value"] for p in out["points"]]; out["stats"]={"total":round(sum(vals),1) if vals else 0,"best":max(vals) if vals else None,"periods":len(vals)}
  return out
