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
st,fl=rq(boss,"GET","/api/flows"); fid=fl[0]["flow_id"]

print("-- anchors --")
st,r=rq(boss,"PUT",f"/api/flows/{fid}/route",{"source_anchor":"right:0","dest_anchor":"left:2"})
ck("valid anchors are stored", st==200 and r["source_anchor"]=="right:0" and r["dest_anchor"]=="left:2",
   f"{st} {r}")
for bad in ["middle:0","top:9","top",'{"x":1}']:
    st,_=rq(boss,"PUT",f"/api/flows/{fid}/route",{"source_anchor":bad})
    ck(f"'{bad}' is refused", st==400, str(st))
st,r=rq(boss,"PUT",f"/api/flows/{fid}/route",{"source_anchor":""})
ck("an empty anchor means automatic, like null", st==200 and r["source_anchor"] is None,
   f"{st} {r.get('source_anchor')}")
st,r=rq(boss,"PUT",f"/api/flows/{fid}/route",{"source_anchor":None})
ck("clearing an anchor returns it to automatic", st==200 and r["source_anchor"] is None, str(r))

print("\n-- bends --")
st,r=rq(boss,"PUT",f"/api/flows/{fid}/route",
        {"waypoints":[{"x":10,"y":20},{"x":30.456,"y":40}]})
ck("bends are stored and rounded", st==200 and json.loads(r["waypoints"])==[{"x":10.0,"y":20.0},{"x":30.46,"y":40.0}],
   str(r["waypoints"]))
st,r=rq(boss,"PUT",f"/api/flows/{fid}/route",{"waypoints":[]})
ck("an empty list clears them", st==200 and r["waypoints"] is None, str(r["waypoints"]))
st,r=rq(boss,"PUT",f"/api/flows/{fid}/route",{"waypoints":[{"x":"a","y":1}]})
ck("a malformed bend is refused", st==400, str(st))
st,r=rq(boss,"PUT",f"/api/flows/{fid}/route",{"waypoints":[{"x":i,"y":i} for i in range(20)]})
ck("too many bends are refused", st==400, str(st))

print("\n-- layout, not content --")
st,before=rq(boss,"GET",f"/api/audit?entity_type=data_flow&entity_id={fid}")
n_before=before["total"]
rq(boss,"PUT",f"/api/flows/{fid}/route",{"source_anchor":"top:1","waypoints":[{"x":5,"y":5}]})
st,after=rq(boss,"GET",f"/api/audit?entity_type=data_flow&entity_id={fid}")
ck("routing writes nothing to the audit log", after["total"]==n_before,
   f"{n_before} -> {after['total']}")

print("\n-- inside a scenario --")
st,sc=rq(boss,"POST","/api/scenarios",{"name":"Route","start_from":"live"})
sid=sc["scenario_id"]
st,sfl=rq(boss,"GET","/api/flows",sc=sid)
sfid=sfl[0]["flow_id"]
st,r=rq(boss,"PUT",f"/api/flows/{sfid}/route",{"source_anchor":"bottom:0"},sc=sid)
ck("a scenario arrow can be routed", st==200 and r["source_anchor"]=="bottom:0", f"{st} {r}")
st,live=rq(boss,"GET","/api/flows")
origin=[f for f in live if f["flow_id"]==[x for x in sfl if x["flow_id"]==sfid][0]["origin_id"]]
ck("the live arrow's routing is untouched",
   origin and origin[0]["source_anchor"]!="bottom:0", str(origin[0]["source_anchor"] if origin else None))
st,ss=rq(boss,"GET","/api/flows",sc=sid)
moved=[f for f in ss if f["flow_id"]==sfid][0]
ck("routing does not mark the flow Modified", moved["change_state"]=="unchanged",
   moved["change_state"])
st,r=rq(boss,"PUT",f"/api/flows/{sfid}/route",{"source_anchor":"top:0"})
ck("a scenario flow cannot be routed from Current Architecture", st==404, str(st))

print("\n-- display name --")
st,r=rq(boss,"PUT",f"/api/flows/{fid}",{"display_name":"Nightly enrolment"})
ck("a display name is stored", st==200 and r["display_name"]=="Nightly enrolment",
   f"{st} {r.get('display_name')}")
st,r=rq(boss,"PUT",f"/api/flows/{fid}",{"display_name":"x"*40})
ck("it is capped at 20 characters", st==200 and len(r["display_name"])==20,
   f"{len(r.get('display_name') or '')}")
st,r=rq(boss,"PUT",f"/api/flows/{fid}",{"display_name":"   "})
ck("blank clears it back to the ID", st==200 and r["display_name"] is None,
   str(r.get("display_name")))
st,made=rq(boss,"POST","/api/flows",
   {"name":"With a label","source_system_id":rq(boss,"GET","/api/systems")[1][0]["system_id"],
    "destination_system_id":rq(boss,"GET","/api/systems")[1][1]["system_id"],
    "frequency":"Daily","display_name":"Short label here that is far too long"})
ck("capped on creation too", st==201, str(st))
if st==201:
    st,fl2=rq(boss,"GET","/api/flows")
    m=[f for f in fl2 if f["flow_id"]==made["flow_id"]][0]
    ck("stored at 20 characters", len(m["display_name"])==20, f"{len(m['display_name'])}")
st,aud=rq(boss,"GET",f"/api/audit?entity_type=data_flow&entity_id={fid}")
ck("a display name IS audited, unlike routing", aud["total"]>0, str(aud["total"]))

print(f"\n  {len(P)} passed, {len(F)} failed")
if F: print("  FAILURES:", F)
