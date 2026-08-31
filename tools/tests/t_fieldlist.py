import json,sys,urllib.request,urllib.error,http.cookiejar,io,uuid
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
def upload(s,url,fields,filename,content,sc=None):
    if sc is not None: url += ("&" if "?" in url else "?") + f"scenario={sc}"
    bnd="----b"+uuid.uuid4().hex; body=io.BytesIO()
    for k,v in fields.items():
        body.write(f"--{bnd}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    body.write(f"--{bnd}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
               f"Content-Type: text/plain\r\n\r\n".encode())
    body.write(content); body.write(f"\r\n--{bnd}--\r\n".encode())
    r=urllib.request.Request(B+url,data=body.getvalue(),method="POST")
    r.add_header("Content-Type",f"multipart/form-data; boundary={bnd}")
    if s["t"]: r.add_header("X-CSRF-Token",s["t"])
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

print("-- Field List on a data flow --")
st,r=rq(boss,"POST",f"/api/resources/data_flow/{fid}/field-list",{})
ck("can be added to a flow", st==200 and any(x["label"]=="Data entry field list" for x in r),
   f"{st} {str(r)[:110]}")
slot=[x for x in r if x["label"]=="Data entry field list"][0]
st,r2=rq(boss,"POST",f"/api/resources/data_flow/{fid}/field-list",{})
ck("adding twice does not duplicate it",
   len([x for x in r2 if x["label"]=="Data entry field list"])==1,
   str(len([x for x in r2 if x["label"]=="Data entry field list"])))

print("\n-- attaching, then clearing --")
st,up=upload(boss,f"/api/resources/{slot['resource_id']}",{},"fields.txt",b"one,two")
ck("a document can be attached to the slot", st in (200,201), f"{st} {str(up)[:110]}")
st,rows=rq(boss,"GET",f"/api/resources/data_flow/{fid}")
now=[x for x in rows if x["resource_id"]==slot["resource_id"]][0]
ck("the slot now shows a file", bool(now.get("filename")), str(now.get("filename")))
st,c=rq(boss,"POST",f"/api/resources/{slot['resource_id']}/clear",{})
ck("it can be cleared", st==200, f"{st} {str(c)[:110]}")
st,rows=rq(boss,"GET",f"/api/resources/data_flow/{fid}")
after=[x for x in rows if x["resource_id"]==slot["resource_id"]][0]
ck("the slot survives, now empty", after.get("filename") in (None,""), str(after.get("filename")))
ck("earlier versions are kept", after.get("version_count",0)>=1, str(after.get("version_count")))
st,c2=rq(boss,"POST",f"/api/resources/{slot['resource_id']}/clear",{})
ck("clearing an empty slot is refused with a reason", st==400, f"{st} {str(c2)[:90]}")

print("\n-- Manual Entry systems get one automatically --")
st,sy=rq(boss,"GET","/api/systems")
store=[x for x in sy if x["system_type"]=="Data Store"][0]["system_id"]
st,me=rq(boss,"POST","/api/systems",
   {"name":"Intake form","system_type":"Manual Entry","managed_by":"R",
    "entry_type":"Direct Entry","target_system_id":store})
st,rows=rq(boss,"GET",f"/api/resources/system/{me['system_id']}")
ck("created without asking", any(x["label"]=="Data entry field list" for x in rows),
   str([x["label"] for x in rows]))

print("\n-- inside a scenario --")
st,sc=rq(boss,"POST","/api/scenarios",{"name":"FL","start_from":"live"})
sid=sc["scenario_id"]
st,sfl=rq(boss,"GET","/api/flows",sc=sid)
sfid=sfl[0]["flow_id"]
st,r=rq(boss,"POST",f"/api/resources/data_flow/{sfid}/field-list",{},sc=sid)
ck("a scenario flow can have one", st==200 and any(x["label"]=="Data entry field list" for x in r),
   f"{st}")
st,liverows=rq(boss,"GET",f"/api/resources/data_flow/{fl[1]['flow_id']}")
ck("it did not appear on the live flow",
   not any(x["label"]=="Data entry field list" for x in liverows),
   str([x["label"] for x in liverows]))

print("\n-- removing a field list --")
st,rows=rq(boss,"GET",f"/api/resources/data_flow/{fid}")
slot2=[x for x in rows if x["label"]=="Data entry field list"][0]
st,r=rq(boss,"DELETE",f"/api/resources/{slot2['resource_id']}")
ck("a flow's field list can be removed entirely", st==200, f"{st} {str(r)[:100]}")
st,rows=rq(boss,"GET",f"/api/resources/data_flow/{fid}")
ck("the slot is gone", not any(x["label"]=="Data entry field list" for x in rows),
   str([x["label"] for x in rows]))
st,mrows=rq(boss,"GET",f"/api/resources/system/{me['system_id']}")
mslot=[x for x in mrows if x["label"]=="Data entry field list"][0]
st,r=rq(boss,"DELETE",f"/api/resources/{mslot['resource_id']}")
ck("a Manual Entry system's field list still cannot be removed", st==400,
   f"{st} {str(r)[:110]}")

print(f"\n  {len(P)} passed, {len(F)} failed")
if F: print("  FAILURES:", F)
