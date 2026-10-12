"""End-to-end test using the official MCP Python SDK stdio client against a temp DB."""
import asyncio,json,os,sys,tempfile
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
SERVER="/home/box/workout/server/server.py"
def show(label,r):
  print(f"\n### {label}  isError={r.is_error}"); print(r.content[0].text[:700])
async def main():
  tmp=tempfile.mkdtemp(); dbp=os.path.join(tmp,"e2e.db")
  params=StdioServerParameters(command="/usr/bin/python3",args=[SERVER,"--stdio","--db",dbp])
  async with stdio_client(params) as (r,w):
    async with ClientSession(r,w) as s:
      init=await s.initialize(); print("server:",init.server_info.name,init.server_info.version,"protocol",init.protocol_version)
      tools=(await s.list_tools()).tools; print("tools:",[t.name for t in tools])
      res=(await s.list_resources()).resources; print("resources:",[(str(x.uri),x.mime_type) for x in res])
      rr=await s.read_resource("ui://workout/hiit.html"); print("resource read:",rr.contents[0].mime_type,len(rr.contents[0].text),"chars")
      ht=[t for t in tools if t.name=="hiit_timer"][0]; print("hiit_timer _meta.ui:",ht.meta["ui"])
      ctx=await s.call_tool("get_context",{}); print("\nonboarding_required:",ctx.structured_content["onboarding_required"])
      show("update_settings",await s.call_tool("update_settings",{"settings":{"unit":"lb","days_per_week":5,"goals":["strength","cardio","hiking"]}}))
      # LLM-parsed versions of: '10 push ups' / 'bench press, 8 reps, 120lb' / 'squat, 8 reps, 100lb' / '卧推 8次 125磅'
      show("'10 push ups'",await s.call_tool("log_sets",{"sets":[{"exercise":"push ups","reps":10,"raw_text":"10 push ups"}]}))
      show("'bench press, 8 reps, 120lb'",await s.call_tool("log_sets",{"sets":[{"exercise":"bench press","reps":8,"weight":120,"unit":"lb","raw_text":"bench press, 8 reps, 120lb"}]}))
      show("'squat, 8 reps, 100lb'",await s.call_tool("log_sets",{"sets":[{"exercise":"squat","reps":8,"weight":100,"unit":"lb","raw_text":"squat, 8 reps, 100lb"}]}))
      show("'卧推 8次 125磅'",await s.call_tool("log_sets",{"sets":[{"exercise":"卧推","reps":8,"weight":125,"unit":"lb","raw_text":"卧推 8次 125磅"}]}))
      show("resolve_exercise",await s.call_tool("resolve_exercise",{"names":["卧推","臥推","深蹲","Push-Ups","胸推"]}))
      show("unknown 'zercher carry'",await s.call_tool("log_sets",{"sets":[{"exercise":"zercher carry","reps":5,"weight":95}]}))
      show("add_alias 胸推",await s.call_tool("add_alias",{"exercise":"Machine Chest Press","alias":"胸推"}))
      show("list_recent",await s.call_tool("list_recent",{"limit":5}))
      show("delete_set (undo)",await s.call_tool("delete_set",{}))
      q=await s.call_tool("query_stats",{"metric":"e1rm","exercise":"卧推","group_by":"day"}); show("query_stats e1rm 卧推",q)
      h=await s.call_tool("hiit_timer",{"name":"Tabata","rounds":8,"segments":[{"kind":"work","seconds":20,"exercise":"Burpee"},{"kind":"rest","seconds":10}]})
      show("hiit_timer",h); print("result _meta:",h.meta)
      show("log_hiit",await s.call_tool("log_hiit",{"hiit_id":h.structured_content["hiit_id"],"completed_rounds":8,"total_seconds":230,"status":"completed"}))
      show("bad input",await s.call_tool("update_settings",{"settings":{"unit":"stone"}}))
      c=(await s.call_tool("get_context",{})).structured_content
      print("\nfinal context: days_trained_this_week=",c["days_trained_this_week"],"sessions=",len(c["recent_sessions"]),"total_sets=",c["total_sets_all_time"])
      assert c["total_sets_all_time"]==3 and c["onboarding_required"]==[]
  print("\nE2E OK (temp db:",dbp,")")
asyncio.run(main())
