import json,re,sys,urllib.request,urllib.error,http.cookiejar,io,uuid
HTML=open("/tmp/h2/static/index.html").read()
m=re.search(r"const UNSCOPED_ENDPOINTS = \[(.*?)\];", HTML, re.S)
UNSCOPED=re.findall(r"'([^']+)'", m.group(1))
def scoped(url, sc):
    if sc is None: return url
    path=url.split("?")[0]
    if not path.startswith("/api/"): return url
    if any(path==p or path.startswith(p+"/") for p in UNSCOPED): return url
    return url + ("&" if "?" in url else "?") + f"scenario={sc}"
B="http://localhost:"+sys.argv[1]
def S():
    j=http.cookiejar.CookieJar()
    return {"op":urllib.request.build_opener(urllib.request.HTTPCookieProcessor(j)),"t":None}
def rq(s,m_,p,b=None,sc=None):
    p=scoped(p,sc)
    d=json.dumps(b).encode() if b is not None else None
    r=urllib.request.Request(B+p,data=d,method=m_)
    if d: r.add_header("Content-Type","application/json")
    if s["t"] and m_!="GET": r.add_header("X-CSRF-Token",s["t"])
    try:
        with s["op"].open(r,timeout=60) as x: return x.status,json.loads(x.read().decode() or "null")
    except urllib.error.HTTPError as e:
        raw=e.read().decode()
        try: return e.code,json.loads(raw)
        except Exception: return e.code,raw[:200]
def upload(s,url,fields,filename,content,sc=None):
    url=scoped(url,sc); bnd="----b"+uuid.uuid4().hex
    body=io.BytesIO()
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
st,sc=rq(boss,"POST","/api/scenarios",{"name":"041","start_from":"live"})
sid=sc["scenario_id"]
st,sy=rq(boss,"GET","/api/systems",sc=sid)
store=[x for x in sy if x["system_type"]=="Data Store"][0]["system_id"]

print("-- Manual Entry inside a scenario --")
st,me=rq(boss,"POST","/api/systems",
   {"name":"Paper intake","system_type":"Manual Entry","managed_by":"Registrar",
    "entry_type":"Direct Entry","target_system_id":store,
    "flow_frequency":"Daily","flow_format":"Keyed entry",
    "flow_transfer_method":"Manual entry"}, sc=sid)
ck("a Manual Entry system saves in a scenario", st==201, f"{st} {str(me)[:150]}")
if st==201:
    st,fl=rq(boss,"GET","/api/flows",sc=sid)
    made=[f for f in fl if f["source_system_id"]==me["system_id"]]
    ck("its inbound flow was created in the scenario", len(made)==1, str(len(made)))
    if made:
        ck("with a scenario-prefixed id", made[0]["flow_id"].startswith(f"S{sid}-"), made[0]["flow_id"])
        ck("inheriting the system's proposed state",
           made[0]["change_state"]==me["change_state"],
           f"{made[0]['change_state']} vs {me['change_state']}")
    st,live=rq(boss,"GET","/api/flows")
    ck("no flow leaked into Current Architecture",
       not [f for f in live if f["source_system_id"]==me["system_id"]])
    st,res=rq(boss,"GET",f"/api/resources/system/{me['system_id']}",sc=sid)
    ck("a Field List slot was created", st==200 and any(r["label"]=="Data entry field list" for r in res),
       f"{st} {str(res)[:90]}")

print("\n-- attaching documents inside a scenario --")
st,r=upload(boss,f"/api/resources/system/{store}",{"label":"Spec"},"spec.txt",b"hello",sc=sid)
ck("document uploads to a scenario system", st in (200,201), f"{st} {str(r)[:130]}")
st,fl=rq(boss,"GET","/api/flows",sc=sid)
st,r2=upload(boss,f"/api/resources/data_flow/{fl[0]['flow_id']}",{"label":"Flow doc"},"f.txt",b"x",sc=sid)
ck("document uploads to a scenario data flow", st in (200,201), f"{st} {str(r2)[:130]}")
# The scenario copy's origin is the live system; check the document did not
# land there.
st,sy2=rq(boss,"GET","/api/systems",sc=sid)
origin=[x for x in sy2 if x["system_id"]==store][0]["origin_id"]
st,rl=rq(boss,"GET",f"/api/resources/system/{origin}")
ck("the document did NOT appear on Current Architecture",
   st==200 and not any(r["label"]=="Spec" for r in rl),
   f"{st} {str(rl)[:110]}")

print("\n-- Method and Format no longer required --")
fid=fl[0]["flow_id"]
st,r=rq(boss,"PUT",f"/api/flows/{fid}",{"transfer_method":"","format":""},sc=sid)
ck("both can be cleared", st==200, f"{st} {str(r)[:120]}")
st,r=rq(boss,"PUT",f"/api/flows/{fid}",{"name":""},sc=sid)
ck("Name is still required", st==400, str(st))
st,r=rq(boss,"PUT",f"/api/flows/{fid}",{"frequency":""},sc=sid)
ck("Frequency is still required", st==400, str(st))

print(f"\n  {len(P)} passed, {len(F)} failed")
if F: print("  FAILURES:", F)
