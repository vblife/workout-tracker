"""E2E (official MCP SDK stdio client, temp DB) for Phases 2-4."""
import asyncio,os,sys,tempfile,json,base64
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
SERVER="/home/box/workout/server/server.py"
def show(l,r,n=420): print(f"\n### {l}  isError={r.is_error}"); print(r.content[0].text[:n])
async def main():
  tmp=tempfile.mkdtemp(); dbp=os.path.join(tmp,"e2e.db")
  env={**os.environ,"WORKOUT_FALLBACK_BASE":"https://cyzhao.github.io/workout-ui"}
  async with stdio_client(StdioServerParameters(command="/usr/bin/python3",args=[SERVER,"--stdio","--db",dbp],env=env)) as (r,w):
    async with ClientSession(r,w) as s:
      i=await s.initialize(); print("server:",i.server_info.name,i.server_info.version)
      tools=(await s.list_tools()).tools; print("tools (%d):"%len(tools),[t.name for t in tools])
      print("UI tools:",{t.name:t.meta["ui"]["resourceUri"] for t in tools if t.meta and "resourceUri" in t.meta.get("ui",{})})
      for res in (await s.list_resources()).resources:
        rr=await s.read_resource(res.uri); print("resource",res.uri,rr.contents[0].mime_type,len(rr.contents[0].text),"chars")
      await s.call_tool("update_settings",{"settings":{"unit":"lb","days_per_week":5,"goals":["strength","cardio","hiking"]}})
      # history: LLM-parsed natural-language logs over 3 weeks
      for d,bw in (("2026-09-22",115),("2026-09-29",120),("2026-10-06",120)):
        await s.call_tool("log_sets",{"performed_at":d+"T07:30:00","sets":[{"exercise":"bench press","reps":12 if d!="2026-10-06" else 12,"weight":bw,"unit":"lb","count":3},
                                       {"exercise":"深蹲","reps":8,"weight":100,"unit":"lb","count":3},{"exercise":"10 push ups".split(" ",1)[1],"reps":10}]})
      show("log_activity hike",await s.call_tool("log_activity",{"type":"hike","duration_min":150,"distance":6.5,"elevation_gain":1800,"performed_at":"2026-10-04T09:00:00"}))
      show("suggest_next 卧推",await s.call_tool("suggest_next",{"exercise":"卧推"}))
      show("suggest_next squat",await s.call_tool("suggest_next",{"exercise":"squat"}))
      show("suggest_next lat pulldown (no history, equipment unknown)",await s.call_tool("suggest_next",{"exercise":"lat pulldown"}))
      d=await s.call_tool("daily_plan_context",{}); sc=d.structured_content
      print("\n### daily_plan_context: focus=",sc["suggested_focus"],"|",sc["rationale"],"| week=",sc["week"],"| equipment_known=",sc["equipment_known"],
            "| candidates=",[(x["exercise"],x["equipment_status"]) for x in sc["candidate_exercises"][:5]])
      h=await s.call_tool("hiit_timer",{"name":"Tabata","rounds":8,"warmup_s":60,"segments":[{"kind":"work","seconds":20,"exercise":"Burpee"},{"kind":"rest","seconds":10}]})
      show("hiit_timer",h,260); print("result _meta:",h.meta)
      out={"hiit":h.structured_content["fallback_url"]}
      for per in ("week","month","quarter","year","all"):
        rp=await s.call_tool("report",{"period":per,"anchor_date":"2026-10-09"}); x=rp.structured_content
        print(f"\n### report {per}: label={x['label']} days={x['summary']['days_trained']} sets={x['summary']['sets']} volume={x['summary']['volume']}{x['unit']} "
              f"cardio={x['summary']['cardio_minutes']}m hikes={x['summary']['hikes']} PRs={len(x['prs'])} highlights={x['highlights']} url_len={len(x['fallback_url'])}")
        out["report_"+per]=x["fallback_url"]
      p=await s.call_tool("show_progress",{"exercise":"bench press","range":"12w"}); x=p.structured_content
      print("\n### show_progress bench e1rm:",[(q["period"],q["value"]) for q in x["points"]],x["stats"]); out["progress"]=x["fallback_url"]
      p=await s.call_tool("show_progress",{"metric":"days","range":"12w"}); print("### show_progress overall days:",p.structured_content["points"])
      show("bad report period",await s.call_tool("report",{"period":"decade"}))
  json.dump(out,open(os.path.join(os.path.dirname(__file__),"fallback_urls.json"),"w"),indent=1)
  print("\nE2E OK")
asyncio.run(main())
