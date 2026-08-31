import json,sys,urllib.request,urllib.error,http.cookiejar
B="http://localhost:"+sys.argv[1]
def S():
    j=http.cookiejar.CookieJar()
    return {"op":urllib.request.build_opener(urllib.request.HTTPCookieProcessor(j)),"t":None}
def rq(s,m,p,b=None,sc=None):
    if sc is not None: p += ("&" if "?" in p else "?") + f"scenario={sc}"
    d=json.dumps(b).encode() if b is not None else None
    r=urllib.request.Request(B+p,data=d,method=m)
    if d: r.add_header("Content-Type","application/json")
    if s["t"] and m!="GET": r.add_header("X-CSRF-Token",s["t"])
    try:
        with s["op"].open(r,timeout=60) as x: return x.status,json.loads(x.read().decode() or "null")
    except urllib.error.HTTPError as e:
        raw=e.read().decode()
        try: return e.code,json.loads(raw)
        except Exception: return e.code,raw[:200]
P=[];F=[]
def ck(l,c,d=""):
    (P if c else F).append(l); print(f"  {'PASS' if c else 'FAIL'}  {l}"+(f"   [{d}]" if not c and d else ""))

boss=S(); st,b=rq(boss,"POST","/api/auth/login",
  {"email":"ben.silberglitt@maine.gov","password":"Bootstrap-Temp-7xQ!"})
boss["t"]=b["csrf_token"]
rq(boss,"POST","/api/auth/change-password",
  {"current_password":"Bootstrap-Temp-7xQ!","new_password":"Quartz-Vault-88#z"})

st,a=rq(boss,"POST","/api/scenarios",{"name":"Plan A","start_from":"live"})
A=a["scenario_id"]
st,sy=rq(boss,"GET","/api/systems",sc=A)
st,newsys=rq(boss,"POST","/api/systems",
   {"name":"Proposed replacement","system_type":"Source","managed_by":"x"},sc=A)
st,fl=rq(boss,"GET","/api/flows",sc=A)
rq(boss,"PUT",f"/api/systems/{sy[0]['system_id']}",{"notes":"tweaked"},sc=A)   # -> modified
rq(boss,"DELETE",f"/api/flows/{fl[0]['flow_id']}?mode=remove",sc=A)            # -> removed
st,before=rq(boss,"GET","/api/systems",sc=A)
st,beforef=rq(boss,"GET","/api/flows",sc=A)
src_states={s["change_state"] for s in before} | {f["change_state"] for f in beforef}
print(f"  source scenario contains states: {sorted(src_states)}")

print("\n-- branching keeps the plan --")
st,c=rq(boss,"POST","/api/scenarios",
        {"name":"Plan B","start_from":"scenario","source_scenario_id":A})
Bid=c["scenario_id"]
ck("branch created", st==201, f"{st} {str(c)[:110]}")
st,after=rq(boss,"GET","/api/systems",sc=Bid)
st,afterf=rq(boss,"GET","/api/flows?include_hidden=1",sc=Bid)
prop_before=sorted(s["name"] for s in before if s["change_state"]=="proposed")
prop_after=sorted(s["name"] for s in after if s["change_state"]=="proposed")
ck("proposed systems stay proposed", prop_before==prop_after and prop_before,
   f"{prop_before} vs {prop_after}")
mod_before=sorted(s["name"] for s in before if s["change_state"]=="modified")
mod_after=sorted(s["name"] for s in after if s["change_state"]=="modified")
ck("modified systems stay modified", mod_before==mod_after and mod_before,
   f"{mod_before} vs {mod_after}")
rem_before=sorted(f["name"] for f in beforef if f["change_state"]=="removed")
rem_after=sorted(f["name"] for f in afterf if f["change_state"]=="removed")
ck("retired flows are carried across", rem_before==rem_after and rem_before,
   f"{rem_before} vs {rem_after}")
ck("system count matches the source", len(after)==len(before), f"{len(after)} vs {len(before)}")

print("\n-- from Current Architecture everything is still unchanged --")
st,d=rq(boss,"POST","/api/scenarios",{"name":"Plan C","start_from":"live"})
C=d["scenario_id"]
st,fresh=rq(boss,"GET","/api/systems",sc=C)
ck("a fresh copy of live starts unchanged",
   {s["change_state"] for s in fresh}=={"unchanged"}, str({s["change_state"] for s in fresh}))

print("\n-- toggling proposed from the details panel --")
tgt=[s for s in after if s["change_state"]=="unchanged"][0]["system_id"]
st,r=rq(boss,"PUT",f"/api/systems/{tgt}",{"proposed":True},sc=Bid)
ck("an existing system can be marked proposed", r.get("change_state")=="proposed",
   str(r.get("change_state")))
st,r=rq(boss,"PUT",f"/api/systems/{tgt}",{"proposed":False},sc=Bid)
ck("and unmarked again", r.get("change_state")=="unchanged", str(r.get("change_state")))
st,flb=rq(boss,"GET","/api/flows",sc=Bid)
fid=[f for f in flb if f["change_state"]!="removed"][0]["flow_id"]
st,r=rq(boss,"PUT",f"/api/flows/{fid}",{"proposed":True},sc=Bid)
ck("a data flow can be toggled too", r.get("change_state")=="proposed", str(r.get("change_state")))
st,live=rq(boss,"GET","/api/systems")
st,r=rq(boss,"PUT",f"/api/systems/{live[0]['system_id']}",{"proposed":True})
ck("but not on Current Architecture", st==400, str(st))

print(f"\n  {len(P)} passed, {len(F)} failed")
if F: print("  FAILURES:", F)
