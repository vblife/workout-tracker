"""Phase 3: progressive overload (double progression + Epley) and daily-plan support."""
import json,math
from datetime import datetime,timedelta,timezone
import core
from core import UserError,LB,TZ,_local,_iso,_parse_time,to_unit,display_unit,get_settings
LOWER={"quads","glutes","hamstrings","calves","legs"}
GROUPS={"push":{"chest","triceps","shoulders"},"pull":{"back","biceps","traps","forearms"},"legs":LOWER,"core":{"core"}}
RECOVERY_H=48

def rep_range(c,override=None):
  s=override or get_settings(c).get("rep_range_default") or "8-12"
  try: lo,hi=(int(x) for x in str(s).split("-")); assert 1<=lo<=hi<=50
  except Exception: raise UserError("rep range must look like '8-12'")
  return lo,hi
def increment(ex,unit):
  """Display-unit weight step: lb -> 5 upper / 10 lower barbell-machine; kg -> exercises.increment_kg."""
  kg=ex["increment_kg"] or 2.5
  if unit=="kg": return kg
  return 10.0 if kg>=5 else 5.0
def rnd(w,step,unit="lb"):
  """Round to loadable weight: 5 lb (2.5 lb plates/side), kg to the exercise step (max 2.5)."""
  q=5.0 if unit=="lb" else min(step,2.5)
  return round(round(w/q)*q,2)
def _sessions_for(c,eid,n=3):
  rows=c.execute("""SELECT session_id,min(performed_at) t FROM sets WHERE exercise_id=? AND is_warmup=0
    GROUP BY session_id ORDER BY t DESC LIMIT ?""",(eid,n)).fetchall()
  out=[]
  for r in rows:
    sets=c.execute("SELECT * FROM sets WHERE session_id=? AND exercise_id=? AND is_warmup=0 ORDER BY id",(r["session_id"],eid)).fetchall()
    out.append({"t":r["t"],"sets":sets})
  return out
def _exrow(c,eid): return c.execute("SELECT * FROM exercises WHERE id=?",(eid,)).fetchone()
def equipment_status(c,ex):
  eq=get_settings(c).get("equipment")
  if ex["is_bodyweight"] or ex["equipment"] in("bodyweight","none"): return "available"
  if not eq: return "unknown"
  return "available" if ex["equipment"] in eq else "unavailable"

def suggest_next(c,exercise,target_rep_range=None,sets=None,now=None):
  eid=core._lookup_exercise(c,exercise); ex=_exrow(c,eid); unit=display_unit(c)
  lo,hi=rep_range(c,target_rep_range); n_sets=sets or 3
  now=now or datetime.now(timezone.utc)
  hist=_sessions_for(c,eid)
  base={"exercise":ex["canonical_name"],"name_zh":ex["name_zh"],"unit":unit,"rep_range":f"{lo}-{hi}",
        "equipment":ex["equipment"],"equipment_status":equipment_status(c,ex)}
  if base["equipment_status"]=="unknown": base["equipment_note"]=f"Equipment not set; this needs {ex['equipment']}. Ask Charles (update_settings equipment)."
  if not hist:
    tip={"reps_weight":f"No history. Start with {n_sets}x{lo} at a weight you could lift ~{hi+2} times; log it and next time I'll progress from there.",
         "reps":f"No history. Do {n_sets} sets of as many good reps as possible (stop 1-2 short of failure).",
         "duration":"No history. Do 3 holds of 30s.","distance":"No history. Start easy: 20-30 min conversational pace."}[ex["tracking"]]
    return {**base,"status":"no_history","sets":n_sets,"reps":lo if ex["tracking"]=="reps_weight" else None,"weight":None,"rationale":tip}
  last=hist[0]; days=(now-_parse_time(last["t"])).days
  L=last["sets"]; last_desc=", ".join(
    f"{s['reps'] or ''}{'x' if s['reps'] else ''}{to_unit(s['weight_kg'],unit) if s['weight_kg'] is not None else 'BW'}" if ex["tracking"] in("reps_weight","reps")
    else f"{s['duration_s'] or 0}s" if ex["tracking"]=="duration" else f"{s['distance_m'] or 0}m" for s in L)
  base.update(last_session_local=_local(last["t"]).strftime("%Y-%m-%d"),days_since=days,last_sets=last_desc)
  if ex["tracking"]=="duration":
    best=max(s["duration_s"] or 0 for s in L); tgt=best+(10 if best<60 else 15)
    return {**base,"status":"progress","sets":max(len(L),3),"duration_s":tgt,"rationale":f"Last best hold {best}s → aim for {tgt}s per set."}
  if ex["tracking"]=="distance":
    return {**base,"status":"progress","rationale":"Cardio: keep weekly time/distance growth ≤10% (see daily_plan_context.cardio).","sets":None}
  reps=[s["reps"] or 0 for s in L]
  if ex["tracking"]=="reps" or all(s["weight_kg"] is None for s in L):
    mx=max(reps)
    if days>14: return {**base,"status":"reset","sets":len(L) or n_sets,"reps":max(1,round(mx*0.8)),"weight":None,"rationale":f"{days} days off → ease back in at ~80% of last reps ({mx})."}
    if mx>=20: return {**base,"status":"progress","sets":len(L)+1 if len(L)<5 else len(L),"reps":mx,"weight":None,
                       "rationale":f"Hit {mx} reps → add a set, or move to a harder variation / add load."}
    return {**base,"status":"progress","sets":max(len(L),n_sets),"reps":mx+1,"weight":None,"rationale":f"Last {last_desc} → add a rep: target {mx+1} per set."}
  w_kg=max(s["weight_kg"] or 0 for s in L); w=to_unit(w_kg,unit); step=increment(ex,unit)
  top=[s for s in L if (s["weight_kg"] or 0)>=w_kg-1e-6]; top_reps=[s["reps"] or 0 for s in top]
  rpes=[s["rpe"] for s in top if s["rpe"] is not None]
  if days>14:
    nw=rnd(w*0.9,step,unit); return {**base,"status":"reset","sets":n_sets,"reps":lo,"weight":nw,
      "rationale":f"{days} days since last session ({last_desc}) → back off ~10% to {nw}{unit} × {lo}."}
  # deload: last two sessions both missed bottom of range at the same/higher weight, or e1RM fell >5%
  if len(hist)>=2:
    prev=hist[1]["sets"]; pw=max(s["weight_kg"] or 0 for s in prev)
    miss=lambda ss,wk: any((s["reps"] or 0)<lo for s in ss if (s["weight_kg"] or 0)>=wk-1e-6)
    e_last=max((s["e1rm_kg"] or 0) for s in L); e_prev=max((s["e1rm_kg"] or 0) for s in prev)
    if (miss(L,w_kg) and miss(prev,pw) and w_kg>=pw-1e-6) or (e_prev and e_last<e_prev*0.95):
      nw=rnd(w*0.9,step,unit); why="missed the bottom of the rep range twice" if miss(L,w_kg) and miss(prev,pw) else f"e1RM dropped {round(100*(1-e_last/e_prev))}%"
      return {**base,"status":"deload","sets":n_sets,"reps":lo+2 if lo+2<=hi else lo,"weight":nw,"rationale":f"{why} → deload 10% to {nw}{unit}, rebuild."}
  if len(top)>=min(n_sets,len(L)) and min(top_reps)>=hi and (not rpes or max(rpes)<=8.5):
    nw=rnd(w+step,step,unit); return {**base,"status":"increase_weight","sets":n_sets,"reps":lo,"weight":nw,
      "rationale":f"All top sets hit {hi}+ reps ({last_desc}) → add {step:g}{unit}: {n_sets}×{lo} @ {nw}{unit}."}
  tgt=min(max(top_reps)+1,hi) if min(top_reps)>=lo else lo
  return {**base,"status":"add_reps","sets":n_sets,"reps":tgt,"weight":w,
    "rationale":f"Last {last_desc} → keep {w}{unit}, aim for {tgt} reps on every set; add weight once all sets reach {hi}."}

def _last_trained(c):
  out={}
  for r in c.execute("SELECT e.muscle_groups,max(x.performed_at) t FROM sets x JOIN exercises e ON e.id=x.exercise_id WHERE x.is_warmup=0 GROUP BY e.id"):
    for m in json.loads(r["muscle_groups"] or "[]"):
      if m not in out or r["t"]>out[m]: out[m]=r["t"]
  return out
def daily_plan_context(c,date=None,now=None):
  """Facts + rule-based focus suggestion for composing today's plan (the LLM writes the plan)."""
  s=get_settings(c); unit=display_unit(c); now=now or datetime.now(timezone.utc)
  day=datetime.fromisoformat(date).date() if date else now.astimezone(TZ).date()
  wk=day-timedelta(days=day.weekday()); wk_utc=_iso(datetime.combine(wk,datetime.min.time(),TZ))
  sess=c.execute("SELECT * FROM sessions WHERE started_at>=? ORDER BY started_at",(wk_utc,)).fetchall()
  days_done=sorted({_local(r["started_at"]).date().isoformat() for r in sess if _local(r["started_at"]).date()<=day})
  trained_today=day.isoformat() in days_done
  target=s.get("days_per_week") or 4; remaining_days=6-day.weekday()+1
  need=max(0,target-len(days_done))
  lt=_last_trained(c); hrs=lambda t:(now-_parse_time(t)).total_seconds()/3600
  groups={}
  for g,ms in GROUPS.items():
    ts=[lt[m] for m in ms if m in lt]; h=min(hrs(t) for t in ts) if ts else None
    groups[g]={"hours_since":None if h is None else round(h),"recovered":h is None or h>=RECOVERY_H}
  cardio_types=("cardio","hike","hiit")
  def cardio_min(a,b):
    m=sum(r["duration_min"] or 0 for r in c.execute("SELECT duration_min FROM sessions WHERE type IN('cardio','hike','hiit') AND started_at>=? AND started_at<?",(a,b)))
    m+=sum((r[0] or 0)/60 for r in c.execute("SELECT x.duration_s FROM sets x JOIN exercises e ON e.id=x.exercise_id WHERE e.category IN('cardio','hike') AND x.performed_at>=? AND x.performed_at<?",(a,b)))
    return round(m)
  wk_next=_iso(datetime.combine(wk+timedelta(days=7),datetime.min.time(),TZ)); prev_wk=_iso(datetime.combine(wk-timedelta(days=7),datetime.min.time(),TZ))
  cm_this,cm_prev=cardio_min(wk_utc,wk_next),cardio_min(prev_wk,wk_utc)
  hikes=[r for r in sess if r["type"]=="hike"]
  # rule-based focus
  strength_days=[r for r in sess if r["type"] in("strength","mixed")]
  rest=False
  last_days=[(day-timedelta(days=i)).isoformat() for i in(1,2,3)]
  if all(d in days_done for d in last_days): rest=True; focus="rest_or_mobility"; why="Trained 3 days in a row → recovery day (walk / mobility)."
  elif need==0 and not trained_today: focus="optional_light"; why=f"Weekly target ({target}) already met → optional easy cardio, hike or mobility."
  elif day.weekday()>=5 and not hikes and "hik" in json.dumps(s.get("goals",[])).lower(): focus="hike"; why="Weekend and no hike yet this week → hike."
  else:
    rec=[g for g in("legs","push","pull") if groups[g]["recovered"]]
    order=sorted(rec,key=lambda g:-(groups[g]["hours_since"] or 10**6))
    if {"push","pull"}<=set(rec) and "legs" not in rec: focus="upper"; why="Legs trained <48h ago; upper body recovered."
    elif "legs" in rec and not({"push","pull"}<=set(rec)): focus="lower"; why="Upper body trained <48h ago; legs recovered."
    elif len(rec)==3: focus="full_body" if len(strength_days)==0 else ("lower" if order[0]=="legs" else "upper"); why="Everything recovered → "+("start the week with full body." if focus=="full_body" else f"{order[0]} least recently trained.")
    else: focus="cardio_hiit"; why="Most muscle groups still recovering → cardio / HIIT day."
    if focus in("upper","lower","full_body") and cm_this<cm_prev*0.5 and day.weekday()>=3: why+=" Cardio behind last week → add 15-20 min finisher or HIIT."
  # candidates
  want={"upper":GROUPS["push"]|GROUPS["pull"],"lower":LOWER,"full_body":LOWER|GROUPS["push"]|GROUPS["pull"],"cardio_hiit":set(),"hike":set(),"rest_or_mobility":set(),"optional_light":set()}[focus]
  cands=[]
  if want:
    rows=c.execute("""SELECT e.*,count(x.id) n,max(x.performed_at) t FROM exercises e LEFT JOIN sets x ON x.exercise_id=e.id
      WHERE e.category='strength' AND e.archived=0 GROUP BY e.id ORDER BY n DESC, e.id""").fetchall()
    for r in rows:
      mus=set(json.loads(r["muscle_groups"] or "[]"))
      if not mus&want: continue
      st=equipment_status(c,r)
      if st=="unavailable": continue
      item={"exercise":r["canonical_name"],"name_zh":r["name_zh"],"equipment":r["equipment"],"equipment_status":st,"times_logged":r["n"],"muscles":sorted(mus)}
      if r["n"]: sg=suggest_next(c,r["id"],now=now); item["suggestion"]={k:sg.get(k) for k in("status","sets","reps","weight","duration_s","rationale")}
      cands.append(item)
    # prefer known exercises, then available ones; cap
    cands.sort(key=lambda i:(-i["times_logged"],i["equipment_status"]!="available"))
    cands=cands[:12]
  return {"date":day.isoformat(),"weekday":day.strftime("%A"),"unit":unit,"settings":{k:s.get(k) for k in("days_per_week","goals","equipment","session_minutes","injuries")},
    "equipment_known":bool(s.get("equipment")),"week":{"days_trained":days_done,"target":target,"still_needed":need,"days_left_incl_today":remaining_days,"trained_today":trained_today,
      "cardio_minutes":cm_this,"cardio_minutes_last_week":cm_prev,"cardio_cap_10pct":round(max(cm_prev,30)*1.1),"hikes":len(hikes)},
    "muscle_recovery":groups,"suggested_focus":focus,"rest_recommended":rest,"rationale":why,"candidate_exercises":cands,
    "notes":[] if s.get("equipment") else ["Equipment unknown: candidates marked equipment_status='unknown' need gym gear; offer bodyweight alternatives or ask Charles."]}
