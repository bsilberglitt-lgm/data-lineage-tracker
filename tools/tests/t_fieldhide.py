"""0.5.1 — field hiding, and the modal conversion that made it possible.

    python3 tools/tests/t_fieldhide.py 8900

Two things are worth protecting here, and they are not the obvious one.

**The required-field guard.** Hiding a field that the Add window validates
would make that record type impossible to create: the form refuses to submit
without a value and no longer draws anywhere to put one. That is a dead end a
user cannot get out of without a SuperAdmin, so it is refused on write *and*
filtered on read — a row could predate a field becoming required.

**No shared scope.** Definitions accept ``entity_type = "*"``; hiding must not.
Whether a field is *used* is a per-record-type question, and one tick emptying
three screens is a worse surprise than three ticks. The same endpoint shape
accepting "*" silently would mean a single click hiding Data Steward from
systems, data flows and data elements at once.

Also covered: the stored value survives hiding (the whole point of the choice
Ben made — a display preference must never destroy data), only a SuperAdmin may
write, everyone may read the lookup, unknown fields are refused, the change is
audited, and the label-to-column map covers every spelling so that the older
field components can resolve themselves from a label alone.
"""
import json, sys, urllib.request, urllib.error, http.cookiejar

B = "http://localhost:" + sys.argv[1]
sys.path.insert(0, ".")
try:
    import field_catalog
except ImportError:                                  # running from /tmp/h2
    sys.path.insert(0, "/tmp/h2")
    import field_catalog


def S():
    cj = http.cookiejar.CookieJar()
    return {"op": urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj)), "t": None}


def rq(s, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if s["t"]:
        headers["X-CSRF-Token"] = s["t"]
    req = urllib.request.Request(B + path, data=data, method=method,
                                 headers=headers)
    try:
        raw = s["op"].open(req).read().decode()
        return 200, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw[:200]


P, F = [], []


def ck(l, c, d=""):
    (P if c else F).append(l)
    print(f"  {'PASS' if c else 'FAIL'}  {l}" + (f"   [{d}]" if not c and d else ""))


boss = S()
st, b = rq(boss, "POST", "/api/auth/login",
           {"email": "ben.silberglitt@maine.gov", "password": "Bootstrap-Temp-7xQ!"})
boss["t"] = b["csrf_token"]
rq(boss, "POST", "/api/auth/change-password",
   {"current_password": "Bootstrap-Temp-7xQ!", "new_password": "Quartz-Vault-88#z"})

# ---------------------------------------------------------------- catalogue
print("-- the catalogue reports hiding and required-ness --")
st, cat = rq(boss, "GET", "/api/field-catalog")
fields = cat.get("fields", [])
ck("the catalogue is served", st == 200 and len(fields) == len(field_catalog.FIELDS),
   f"{st} {len(fields)}")
ck("every field says whether it is hidden",
   all("hidden" in f for f in fields))
ck("every field says whether it is required",
   all("required" in f for f in fields))
ck("nothing is hidden to begin with",
   not any(f["hidden"] for f in fields))

req = [f for f in fields if f["required"]]
ck("some fields are required", len(req) > 0, str(len(req)))
ck("a required field is one the Add window validates",
   {(f["entity"], f["column"]) for f in req} ==
   {(f["entity"], f["column"]) for f in field_catalog.FIELDS if f.get("required")})

# A field that is safe to hide: optional, and on systems.
victim = next(f for f in fields
              if f["entity"] == "system" and not f["required"]
              and f["column"] == "vendor")

# ------------------------------------------------------------------- hiding
print("-- hiding and restoring --")
st, r = rq(boss, "PUT", "/api/field-visibility",
           {"entity": "system", "column": "vendor", "hidden": True})
ck("a SuperAdmin can hide a field", st == 200 and r.get("hidden") is True, str(st))

st, cat = rq(boss, "GET", "/api/field-catalog")
now = {(f["entity"], f["column"]): f["hidden"] for f in cat["fields"]}
ck("the catalogue reports it hidden", now[("system", "vendor")] is True)
ck("the same column on another record type is untouched",
   now.get(("data_flow", "organization")) is False)

st, look = rq(boss, "GET", "/api/field-definitions")
ck("the hidden set names it", "system|vendor" in look.get("hidden", []),
   str(look.get("hidden")))
ck("the hidden set is keyed on the column, not a label",
   all("|" in k and k.split("|", 1)[1].islower() or "_" in k
       for k in look.get("hidden", [])))

st, r = rq(boss, "PUT", "/api/field-visibility",
           {"entity": "system", "column": "vendor", "hidden": False})
ck("it can be restored", st == 200 and r.get("hidden") is False, str(st))
st, look = rq(boss, "GET", "/api/field-definitions")
ck("and leaves the hidden set", "system|vendor" not in look.get("hidden", []))

# ------------------------------------------------- the value is never touched
print("-- a hidden field keeps its data --")
st, made = rq(boss, "POST", "/api/systems",
              {"name": "Hide Test Store", "system_type": "Data Store",
               "managed_by": "QA", "vendor": "Acme Corp"})
sid = made.get("system_id") or made.get("id")
ck("a system was created to test with", st == 200 and sid, f"{st} {made}")

rq(boss, "PUT", "/api/field-visibility",
   {"entity": "system", "column": "vendor", "hidden": True})
st, got = rq(boss, "GET", f"/api/systems/{sid}")
ck("the stored value survives hiding", got.get("vendor") == "Acme Corp",
   repr(got.get("vendor")))
rq(boss, "PUT", "/api/field-visibility",
   {"entity": "system", "column": "vendor", "hidden": False})
st, got = rq(boss, "GET", f"/api/systems/{sid}")
ck("and is intact after restoring", got.get("vendor") == "Acme Corp",
   repr(got.get("vendor")))

# ----------------------------------------------------------------- refusals
print("-- what must be refused --")
victim_req = req[0]
st, r = rq(boss, "PUT", "/api/field-visibility",
           {"entity": victim_req["entity"], "column": victim_req["column"],
            "hidden": True})
ck("a required field cannot be hidden", st == 400, str(st))
ck("and the refusal names the field and record type",
   isinstance(r, dict) and victim_req["label"] in json.dumps(r), json.dumps(r)[:120])

st, r = rq(boss, "PUT", "/api/field-visibility",
           {"entity": "*", "column": "data_steward_id", "hidden": True})
ck("the shared scope is refused for hiding", st == 400, str(st))
st, look = rq(boss, "GET", "/api/field-definitions")
ck("and nothing was hidden by it",
   not any(k.startswith("*|") for k in look.get("hidden", [])))

st, r = rq(boss, "PUT", "/api/field-visibility",
           {"entity": "system", "column": "not_a_real_column", "hidden": True})
ck("an unknown column is refused", st == 400, str(st))
st, r = rq(boss, "PUT", "/api/field-visibility",
           {"entity": "unicorn", "column": "name", "hidden": True})
ck("an unknown record type is refused", st == 400, str(st))

# ------------------------------------------------------------- the lookup map
print("-- labels resolve to columns --")
st, look = rq(boss, "GET", "/api/field-definitions")
cols = look.get("columns", {})
missing = []
for f in field_catalog.FIELDS:
    for key in ("label", "read_label", "modal_label"):
        if f.get(key):
            if cols.get(f"{f['entity']}|{f[key]}") != f["column"]:
                missing.append(f"{f['entity']}|{f[key]}")
ck("every spelling of every field maps to its column", not missing,
   ", ".join(missing[:4]))

# The point of the map: a column with more than one spelling must resolve from
# any of them, or hiding would work in one view and not another.
multi = [f for f in field_catalog.FIELDS
         if len({f[k] for k in ("label", "read_label", "modal_label") if f.get(k)}) > 1]
ck("fields with several spellings exist to test", len(multi) > 5, str(len(multi)))
bad = [f["column"] for f in multi
       if len({cols.get(f"{f['entity']}|{f[k]}")
               for k in ("label", "read_label", "modal_label") if f.get(k)}) != 1]
ck("each resolves to one column from every spelling", not bad, str(bad[:4]))

# --------------------------------------------------------------- permissions
print("-- permissions --")
email, pw = "hide.reader@maine.gov", "Thistle-Harbor-52#k"
st, existing = rq(boss, "GET", "/api/users")
found = next((u for u in existing if u["email"] == email), None) \
    if isinstance(existing, list) else None
if found:
    rq(boss, "PUT", f"/api/users/{found['user_id']}",
       {"is_active": 1, "password": pw, "role": "Viewer"})
else:
    rq(boss, "POST", "/api/users", {"email": email, "first_name": "Wren",
                                    "last_name": "Pike", "role": "Viewer",
                                    "password": pw})

viewer = S()
st, vb = rq(viewer, "POST", "/api/auth/login", {"email": email, "password": pw})
ck("the Viewer can sign in", st == 200, str(st))
if st == 200:
    viewer["t"] = vb["csrf_token"]
    if vb["user"].get("must_change_password"):
        rq(viewer, "POST", "/api/auth/change-password",
           {"current_password": pw, "new_password": "Bramble-Quay-71#v"})

    st, look = rq(viewer, "GET", "/api/field-definitions")
    ck("a Viewer may read the hidden set, since it decides what they see",
       st == 200 and "hidden" in look, str(st))
    st, _ = rq(viewer, "GET", "/api/field-catalog")
    ck("a Viewer may not read the whole catalogue", st == 403, str(st))
    st, _ = rq(viewer, "PUT", "/api/field-visibility",
               {"entity": "system", "column": "notes", "hidden": True})
    ck("a Viewer may not hide a field", st == 403, str(st))
    st, look = rq(boss, "GET", "/api/field-definitions")
    ck("and did not manage to", "system|notes" not in look.get("hidden", []))

# --------------------------------------------------------------------- audit
print("-- the change is audited --")
rq(boss, "PUT", "/api/field-visibility",
   {"entity": "data_flow", "column": "legal_authority", "hidden": True})
st, audit = rq(boss, "GET", "/api/audit?limit=40")
events = audit.get("events", audit if isinstance(audit, list) else [])
hid = [e for e in events if e.get("entity_type") == "field_hidden"]
ck("hiding writes an audit event", len(hid) > 0, str(len(hid)))
ck("the event says what happened, in words",
   any("Legal Authority" in (e.get("summary") or "") for e in hid),
   "; ".join((e.get("summary") or "") for e in hid[:3]))

rq(boss, "PUT", "/api/field-visibility",
   {"entity": "data_flow", "column": "legal_authority", "hidden": False})
st, audit = rq(boss, "GET", "/api/audit?limit=40")
events = audit.get("events", audit if isinstance(audit, list) else [])
restored = [e for e in events if e.get("entity_type") == "field_hidden"
            and "Restored" in (e.get("summary") or "")]
ck("restoring writes its own event", len(restored) > 0,
   str(len(restored)))

# Setting it to what it already is should not fill the trail with noise.
before = len([e for e in events if e.get("entity_type") == "field_hidden"])
rq(boss, "PUT", "/api/field-visibility",
   {"entity": "data_flow", "column": "legal_authority", "hidden": False})
st, audit = rq(boss, "GET", "/api/audit?limit=40")
events = audit.get("events", audit if isinstance(audit, list) else [])
after = len([e for e in events if e.get("entity_type") == "field_hidden"])
ck("an unchanged setting writes nothing", after == before, f"{before} -> {after}")

# ------------------------------------------------- hiding and defining differ
print("-- hiding and defining are independent --")
rq(boss, "PUT", "/api/field-catalog",
   {"entity": "system", "column": "notes", "definition": "Anything else worth saying."})
rq(boss, "PUT", "/api/field-visibility",
   {"entity": "system", "column": "notes", "hidden": True})
st, look = rq(boss, "GET", "/api/field-definitions")
ck("a hidden field keeps its definition",
   look["definitions"].get("system|Notes") == "Anything else worth saying.",
   repr(look["definitions"].get("system|Notes")))
ck("and is still reported hidden", "system|notes" in look.get("hidden", []))
rq(boss, "PUT", "/api/field-visibility",
   {"entity": "system", "column": "notes", "hidden": False})

print(f"\n  {len(P)} passed, {len(F)} failed")
sys.exit(1 if F else 0)
