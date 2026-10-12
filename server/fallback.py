"""Plain-https fallback links for clients that do not render MCP Apps (e.g. iPhone).
The page is a static copy of the same UI HTML; all data rides in the URL #fragment
(base64url JSON), which browsers never send to the web server."""
import base64,json,os
import core
MAX_URL=7000
def base_url(c):
  b=os.environ.get("WORKOUT_FALLBACK_BASE") or core.get_settings(c).get("fallback_base_url")
  return b.rstrip("/") if b else None
def encode(payload):
  raw=json.dumps(payload,ensure_ascii=False,separators=(",",":")).encode()
  return base64.urlsafe_b64encode(raw).decode().rstrip("=")
def decode(frag):
  return json.loads(base64.urlsafe_b64decode(frag+"="*(-len(frag)%4)))
def _shrink(p):
  p=json.loads(json.dumps(p))
  for key in("prs","lifts","candidate_exercises"):
    if isinstance(p.get(key),list): p[key]=p[key][-5:]
  if isinstance(p.get("points"),list): p["points"]=p["points"][-60:]
  if isinstance(p.get("series"),dict): p["series"]["points"]=p["series"]["points"][-60:]
  p.get("summary",{}).pop("sets_by_muscle",None); return p
def link(c,page,payload):
  b=base_url(c)
  if not b: return None
  u=f"{b}/{page}.html#{encode(payload)}"
  if len(u)>MAX_URL: u=f"{b}/{page}.html#{encode(_shrink(payload))}"
  return u if len(u)<=MAX_URL*2 else None
