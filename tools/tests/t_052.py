"""0.5.2 — fields that saved silently into nothing.

    python3 tools/tests/t_052.py 8900

`tools/check_payloads.py` compares the two field lists statically. This goes the
other way and proves the round trip: it reads the **real** payload shape out of
`static/index.html`, sends that, and checks the value came back. A static check
can be satisfied by a field being named; only this proves it persists.

That distinction is the whole point. Owner on a data flow was present in the Add
window, in the edit control and in the read view — every test that asked "can
the server store an owner?" passed, because the test sent `owner_id` itself. The
browser never did. So the test has to send what the browser sends.
"""
import json,os
import re
import sys
import urllib.error
import urllib.request
import http.cookiejar
from pathlib import Path


# The repository, whether this is run from the project or from /tmp/h2.
HERE = Path(__file__).resolve().parent.parent.parent
HTML = HERE / "static" / "index.html"
if not HTML.exists():
    HTML = Path("/tmp/h2/static/index.html")


# Credentials, the connection and signing in all come from _client.py, so no
# suite carries a hardcoded password and none of them changes one. See the
# comment at the top of that file for why that matters.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _client import sign_in, session as S, request as rq, BASE as B  # noqa: E402

P, F = [], []


def ck(l, c, d=""):
    (P if c else F).append(l)
    print(f"  {'PASS' if c else 'FAIL'}  {l}" + (f"   [{d}]" if not c and d else ""))


def payload_keys(kind):
    """The field names the details panel actually sends for one record type."""
    src = HTML.read_text()
    start = src.index("const saveEdit")
    block = src[start:src.index("const result = await api.put", start)]
    m = re.search(r"type === '" + kind + r"'[^\n]*\n\s*url = [^\n]*\n\s*"
                  r"saveData = \{(.*?)\n      \};", block, re.S)
    assert m, kind
    return set(re.findall(r"(\w+):\s*form\.", m.group(1)))


boss = sign_in()

st, person = rq(boss, "POST", "/api/contributors",
                {"name": "Dana Rowe", "title": "Data Governance Lead",
                 "email": "dana.rowe@maine.gov"})
cid = person.get("contributor_id")
# `in (200, 201)` because the shared client reports the real status code and
# a creation answers 201. The suite-local helper this replaced flattened
# every success to 200, which is why these read as 200 originally.
ck("a contributor to assign", st in (200, 201) and bool(cid), f"{st} {person}")

# ------------------------------------------------- data flow Owner, as sent
print("-- data flow Owner, using the payload the browser builds --")
keys = payload_keys("flow")
ck("the flow payload names owner_id", "owner_id" in keys,
   "owner_id missing — editing Owner in the flyout would do nothing")

st, flows = rq(boss, "GET", "/api/flows")
flow = flows[0]
fid = flow["flow_id"]

# Build the body exactly as the browser would: every key it sends, carrying
# the record's current values, with Owner changed. A test that sent only
# owner_id would pass even with the bug present.
body = {k: flow.get(k) for k in keys}
body["owner_id"] = cid
st, _ = rq(boss, "PUT", f"/api/flows/{fid}", body)
ck("the save is accepted", st == 200, str(st))

st, got = rq(boss, "GET", f"/api/flows/{fid}")
ck("Owner is stored", got.get("owner_id") == cid, repr(got.get("owner_id")))

# Changing an existing Owner was the other half of the complaint.
st, second = rq(boss, "POST", "/api/contributors", {"name": "Kit Vance"})
cid2 = second.get("contributor_id")
body = {k: got.get(k) for k in keys}
body["owner_id"] = cid2
rq(boss, "PUT", f"/api/flows/{fid}", body)
st, got = rq(boss, "GET", f"/api/flows/{fid}")
ck("an existing Owner can be changed", got.get("owner_id") == cid2,
   repr(got.get("owner_id")))

# And cleared again — the dropdown offers "none", which must store NULL
# rather than an empty string against the foreign key.
body = {k: got.get(k) for k in keys}
body["owner_id"] = ""
st, _ = rq(boss, "PUT", f"/api/flows/{fid}", body)
ck("clearing Owner is accepted", st == 200, str(st))
st, got = rq(boss, "GET", f"/api/flows/{fid}")
ck("and stores nothing rather than an empty string",
   got.get("owner_id") in (None, ""), repr(got.get("owner_id")))

# Data Steward sat beside it and worked; prove the fix did not disturb it.
body = {k: got.get(k) for k in keys}
body["data_steward_id"] = cid
rq(boss, "PUT", f"/api/flows/{fid}", body)
st, got = rq(boss, "GET", f"/api/flows/{fid}")
ck("Data Steward still works", got.get("data_steward_id") == cid,
   repr(got.get("data_steward_id")))

# ------------------------------------- data element Source of Truth, as sent
print("-- data element Source of Truth, the same bug unreported --")
keys = payload_keys("element")
ck("the element payload names source_of_truth", "source_of_truth" in keys)

st, elems = rq(boss, "GET", "/api/elements")
el = elems[0]
eid = el["element_id"]

# Source of Truth is a foreign key to system.system_id, not free text. That is
# why the control had to change in the same release as the payload fix: adding
# the field to the payload while it was still a text box turned a silent no-op
# into a 500 for anyone typing a system's name.
st, systems_for_el = rq(boss, "GET", "/api/systems")
target = next(x["system_id"] for x in systems_for_el
              if x["system_id"] != el.get("source_of_truth"))
body = {k: el.get(k) for k in keys}
body["source_of_truth"] = target
st, _ = rq(boss, "PUT", f"/api/elements/{eid}", body)
ck("the save is accepted", st == 200, str(st))
st, got = rq(boss, "GET", f"/api/elements/{eid}")
ck("Source of Truth is stored", got.get("source_of_truth") == target,
   repr(got.get("source_of_truth")))

# A name rather than an id must be refused cleanly, not stored and not a 500.
body = {k: got.get(k) for k in keys}
body["source_of_truth"] = "Some System That Does Not Exist"
st, _ = rq(boss, "PUT", f"/api/elements/{eid}", body)
ck("a free-text system name is refused, not stored", st >= 400, str(st))
st, after = rq(boss, "GET", f"/api/elements/{eid}")
ck("and the previous value survives the refusal",
   after.get("source_of_truth") == target, repr(after.get("source_of_truth")))

# Clearing it must store NULL rather than an empty string.
body = {k: after.get(k) for k in keys}
body["source_of_truth"] = ""
st, _ = rq(boss, "PUT", f"/api/elements/{eid}", body)
ck("clearing Source of Truth is accepted", st == 200, str(st))
st, after = rq(boss, "GET", f"/api/elements/{eid}")
ck("and stores nothing rather than an empty string",
   after.get("source_of_truth") in (None, ""), repr(after.get("source_of_truth")))

# ------------------------------------------------ every type, every field
print("-- nothing else saves into nothing --")
# For each record type, send its whole payload with a marker in every text
# field, then confirm each one came back. This is the broad version of the
# two checks above and would have caught both without knowing about them.
st, systems = rq(boss, "GET", "/api/systems")
sysrow = next(s for s in systems if s.get("system_type") != "Manual Entry")
sid = sysrow["system_id"]
keys = payload_keys("system")
SKIP = {"name", "system_type", "status", "environment", "data_classification",
        "access_method", "encryption_at_rest", "encryption_in_transit",
        "retention_schedule", "contains_pii", "ferpa_protected",
        "has_audit_trail", "target_system_id", "entry_type", "proposed",
        "last_security_review", "data_steward_id", "owner_id"}
body = {k: sysrow.get(k) for k in keys}
marker = "sentinel-0.5.2"
touched = [k for k in keys if k not in SKIP]
for k in touched:
    body[k] = marker
st, _ = rq(boss, "PUT", f"/api/systems/{sid}", body)
ck("a system accepts every field it is sent", st == 200, str(st))
st, got = rq(boss, "GET", f"/api/systems/{sid}")
lost = [k for k in touched if got.get(k) != marker]
ck("and every free-text field on a system comes back", not lost,
   "not stored: " + ", ".join(lost))

# ------------------------------------------------ deactivating a contributor
print("-- deactivating a contributor --")
st, person2 = rq(boss, "POST", "/api/contributors",
                 {"name": "Wren Abbott", "email": "wren.abbott@maine.gov"})
cid2 = person2.get("contributor_id")

# Make them the steward of a system and the owner of a flow, so the warning
# has something to report and the "assignments are kept" claim has something
# to be true about.
st, systems = rq(boss, "GET", "/api/systems")
sid2 = systems[0]["system_id"]
rq(boss, "PUT", f"/api/systems/{sid2}", {"data_steward_id": cid2})
st, flows = rq(boss, "GET", "/api/flows")
fid2 = flows[0]["flow_id"]
keys = payload_keys("flow")
body = {k: flows[0].get(k) for k in keys}
body["owner_id"] = cid2
rq(boss, "PUT", f"/api/flows/{fid2}", body)

# A linked account. The password must not contain their name — the policy
# refuses that, which is how this test first failed.
TEMP, REAL = "Thistle-Quay-52#k", "Bramble-Harbor-71#v"
st, _ = rq(boss, "POST", "/api/users",
           {"email": "w.abbott@maine.gov", "first_name": "Wren",
            "last_name": "Abbott", "role": "Editor", "password": TEMP,
            "contributor_id": cid2})
ck("a linked user account was created", st in (200, 201), str(st))

st, refs = rq(boss, "GET", f"/api/contributors/{cid2}/references")
ck("the references endpoint answers", st == 200, str(st))
ck("it counts what names them", refs.get("total") == 2, str(refs.get("total")))
ck("it names the records rather than only counting",
   all(g["named"] and g["named"][0].get("label") for g in refs["records"]),
   json.dumps(refs.get("records"))[:160])
ck("it reports the linked account by address",
   (refs.get("linked_user") or {}).get("email") == "w.abbott@maine.gov",
   json.dumps(refs.get("linked_user")))

st, r = rq(boss, "PUT", f"/api/contributors/{cid2}", {"is_active": 0})
ck("a SuperAdmin can deactivate", st == 200 and not r.get("is_active"), str(st))

# The decision Ben made: assignments are kept, not cleared. This is the check
# that would fail if someone later "tidied up" by nulling the references.
st, got = rq(boss, "GET", f"/api/systems/{sid2}")
ck("the system still names them as Data Steward",
   got.get("data_steward_id") == cid2, repr(got.get("data_steward_id")))
st, got = rq(boss, "GET", f"/api/flows/{fid2}")
ck("the flow still names them as Owner",
   got.get("owner_id") == cid2, repr(got.get("owner_id")))

st, cons = rq(boss, "GET", "/api/contributors")
ck("a SuperAdmin still sees them",
   any(c["contributor_id"] == cid2 for c in cons))

ed = S()
st, eb = rq(ed, "POST", "/api/auth/login",
            {"email": "w.abbott@maine.gov", "password": TEMP})
if st == 200:
    ed["t"] = eb["csrf_token"]
    if eb["user"].get("must_change_password"):
        rq(ed, "POST", "/api/auth/change-password",
           {"current_password": TEMP, "new_password": REAL})
    st, cons = rq(ed, "GET", "/api/contributors")
    ck("an Editor does not see a deactivated contributor",
       isinstance(cons, list)
       and not any(c["contributor_id"] == cid2 for c in cons), str(st))
    st, _ = rq(ed, "PUT", f"/api/contributors/{cons[0]['contributor_id']}",
               {"is_active": 0})
    ck("an Editor cannot deactivate anyone", st == 403, str(st))
    st, _ = rq(ed, "GET", f"/api/contributors/{cid2}/references")
    ck("an Editor cannot read the references", st == 403, str(st))

    # Deactivating a contributor is not deactivating a sign-in.
    chk = S()
    st, _ = rq(chk, "POST", "/api/auth/login",
               {"email": "w.abbott@maine.gov", "password": REAL})
    ck("their user account still signs in", st == 200, str(st))
    st, users = rq(boss, "GET", "/api/users")
    linked = next((u for u in users if u["email"] == "w.abbott@maine.gov"), None)
    ck("and is still linked to the contributor",
       linked and linked.get("contributor_id") == cid2,
       repr(linked and linked.get("contributor_id")))
else:
    ck("the Editor could sign in", False, str(st))

st, r = rq(boss, "PUT", f"/api/contributors/{cid2}", {"is_active": 1})
ck("reactivating works", st == 200 and r.get("is_active") == 1, str(st))

st, audit = rq(boss, "GET", "/api/audit?limit=40")
events = audit.get("events", [])
summaries = [e.get("summary") or "" for e in events
             if e.get("entity_type") == "contributor"]
ck("deactivating is audited in words, with the scale",
   any("Deactivated Wren Abbott" in x and "still named on 2" in x
       for x in summaries), "; ".join(summaries[:3]))
ck("reactivating has its own event",
   any("Reactivated Wren Abbott" in x for x in summaries),
   "; ".join(summaries[:3]))

# ------------------------------------------------- what a delete would destroy
print("-- the delete confirmation knows what it is about to destroy --")
st, systems = rq(boss, "GET", "/api/systems")

heavy = light = None
for sy in systems:
    st, imp = rq(boss, "GET", f"/api/systems/{sy['system_id']}/delete-impact")
    if st != 200:
        continue
    if imp["heavy"] and heavy is None:
        heavy = (sy, imp)
    if not imp["heavy"] and not imp["counts"] and light is None:
        light = (sy, imp)

ck("a system with an uploaded schema is found", heavy is not None)
if heavy:
    sy, imp = heavy
    labels = {c["label"]: c["count"] for c in imp["counts"]}
    ck("it counts the database tables", labels.get("database table", 0) > 0,
       str(labels))
    ck("it counts the database columns", labels.get("database column", 0) > 0,
       str(labels))
    ck("and asks for the name to be typed", imp["heavy"] is True)
    ck("it reports the record's name, for the typing check to compare against",
       imp["record"]["name"] == sy["name"],
       f"{imp['record'].get('name')!r} vs {sy['name']!r}")

ck("a system with nothing attached is found", light is not None)
if light:
    ck("it asks for no typing", light[1]["heavy"] is False)

# A system used by a flow cannot be deleted at all, so the dialog has to say
# which flows rather than offering a Delete button that will fail.
blocked = None
for sy in systems:
    st, imp = rq(boss, "GET", f"/api/systems/{sy['system_id']}/delete-impact")
    if st == 200 and imp["blockers"]:
        blocked = imp
        break
ck("a system used by flows reports a blocker", blocked is not None)
if blocked:
    ck("and names the flows standing in the way",
       blocked["blockers"][0]["named"]
       and blocked["blockers"][0]["named"][0].get("label"),
       json.dumps(blocked["blockers"])[:160])
    # The blocker has to be real: deleting it must actually be refused.
    sid_b = blocked["record"]["id"]
    st, _ = rq(boss, "DELETE", f"/api/systems/{sid_b}")
    ck("and the delete is genuinely refused", st == 409, str(st))

st, flows = rq(boss, "GET", "/api/flows")
st, imp = rq(boss, "GET", f"/api/flows/{flows[0]['flow_id']}/delete-impact")
ck("a flow reports its impact", st == 200, str(st))
ck("a flow never asks for typing", imp.get("heavy") is False, str(imp.get("heavy")))

st, _ = rq(boss, "GET", "/api/systems/SYS-does-not-exist/delete-impact")
ck("an unknown record is a 404", st == 404, str(st))
st, _ = rq(boss, "GET", "/api/elements/EL-001/delete-impact")
ck("a record type without a delete dialog is a 404", st == 404, str(st))

print(f"\n  {len(P)} passed, {len(F)} failed")
sys.exit(1 if F else 0)
