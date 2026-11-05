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
import os

# `field_catalog` is generated into the project root and has no dependencies,
# so importing it is safe anywhere — but only if it can be found. Looked for
# relative to THIS FILE first, so the suite does not depend on being run from
# the project root, then relative to the working directory, then the flat copy
# the assistant's harness makes.
for _cand in (os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."),
              os.getcwd(), os.path.dirname(os.path.abspath(__file__)), "/tmp/h2"):
    if os.path.exists(os.path.join(_cand, "field_catalog.py")):
        sys.path.insert(0, _cand)
        break
import field_catalog  # noqa: E402


# Credentials, the connection and signing in all come from _client.py, so no
# suite carries a hardcoded password and none of them changes one. See the
# comment at the top of that file for why that matters.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _client import sign_in, session as S, request as rq, BASE as B  # noqa: E402

P, F = [], []


def ck(l, c, d=""):
    (P if c else F).append(l)
    print(f"  {'PASS' if c else 'FAIL'}  {l}" + (f"   [{d}]" if not c and d else ""))


boss = sign_in()

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
# **Not "nothing is hidden to begin with".** That assertion demanded a
# database nobody had used, and failed the moment Ben ran this against his own
# local app, where he had hidden a field in Preferences while testing the
# feature — a correct setting of his, reported as a test failure.
#
# What is worth asserting is the invariant: the catalogue and
# /api/field-definitions must agree about what is hidden. They are keyed
# differently — the catalogue by (entity, column), the lookup by
# "entity|column" — and a disagreement between them is a real fault, which is
# the thing the original check was reaching for.
ORIGINALLY_HIDDEN = {(f["entity"], f["column"]) for f in fields if f["hidden"]}
st, look0 = rq(boss, "GET", "/api/field-definitions")
ck("the catalogue and the lookup agree about what is hidden",
   {tuple(k.split("|", 1)) for k in look0.get("hidden", [])} == ORIGINALLY_HIDDEN,
   f"catalogue {sorted(ORIGINALLY_HIDDEN)} vs lookup {sorted(look0.get('hidden', []))}")
if ORIGINALLY_HIDDEN:
    print(f"     (note: {len(ORIGINALLY_HIDDEN)} field(s) already hidden on this "
          f"database — left exactly as found)")


def was(entity, column):
    """Whether this field was hidden before the suite started.

    Restores pass this rather than `False`, so a field its owner had
    deliberately hidden is put back hidden.
    """
    return (entity, column) in ORIGINALLY_HIDDEN

req = [f for f in fields if f["required"]]
ck("some fields are required", len(req) > 0, str(len(req)))
ck("a required field is one the Add window validates",
   {(f["entity"], f["column"]) for f in req} ==
   {(f["entity"], f["column"]) for f in field_catalog.FIELDS if f.get("required")})

# A field that is safe to hide: optional, on systems, and **not already hidden
# by whoever owns this database**. Hiding one that is already hidden would make
# the restore at the end un-hide a setting the owner meant to keep.
victim = next(f for f in fields
              if f["entity"] == "system" and not f["required"]
              and not f["hidden"]
              and (f["entity"], f["column"]) not in ORIGINALLY_HIDDEN)
VE, VC = victim["entity"], victim["column"]

# ------------------------------------------------------------------- hiding
print("-- hiding and restoring --")
st, r = rq(boss, "PUT", "/api/field-visibility",
           {"entity": VE, "column": VC, "hidden": True})
ck("a SuperAdmin can hide a field", st == 200 and r.get("hidden") is True, str(st))

st, cat = rq(boss, "GET", "/api/field-catalog")
now = {(f["entity"], f["column"]): f["hidden"] for f in cat["fields"]}
ck("the catalogue reports it hidden", now[(VE, VC)] is True)
ck("the same column on another record type is untouched",
   now.get(("data_flow", "organization")) is False)

st, look = rq(boss, "GET", "/api/field-definitions")
ck("the hidden set names it", f"{VE}|{VC}" in look.get("hidden", []),
   str(look.get("hidden")))
ck("the hidden set is keyed on the column, not a label",
   all("|" in k and k.split("|", 1)[1].islower() or "_" in k
       for k in look.get("hidden", [])))

st, r = rq(boss, "PUT", "/api/field-visibility",
           {"entity": VE, "column": VC, "hidden": False})
ck("it can be restored", st == 200 and r.get("hidden") is False, str(st))
st, look = rq(boss, "GET", "/api/field-definitions")
ck("and leaves the hidden set", f"{VE}|{VC}" not in look.get("hidden", []))

# ------------------------------------------------- the value is never touched
print("-- a hidden field keeps its data --")
st, made = rq(boss, "POST", "/api/systems",
              {"name": "Hide Test Store", "system_type": "Data Store",
               "managed_by": "QA", "vendor": "Acme Corp"})
sid = made.get("system_id") or made.get("id")
# 201 on creation: the shared client reports real status codes.
ck("a system was created to test with", st in (200, 201) and sid, f"{st} {made}")

# This block needs a field that holds a value, so it is specifically `vendor`
# rather than the victim chosen above. Every restore below therefore puts the
# field back to whatever it was before this suite ran, NOT unconditionally to
# visible — forcing it visible would quietly undo its owner's own setting.
rq(boss, "PUT", "/api/field-visibility",
   {"entity": "system", "column": "vendor", "hidden": True})
st, got = rq(boss, "GET", f"/api/systems/{sid}")
ck("the stored value survives hiding", got.get("vendor") == "Acme Corp",
   repr(got.get("vendor")))
rq(boss, "PUT", "/api/field-visibility",
   {"entity": "system", "column": "vendor", "hidden": was("system", "vendor")})
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
   {"entity": "data_flow", "column": "legal_authority",
    "hidden": was("data_flow", "legal_authority")})
st, audit = rq(boss, "GET", "/api/audit?limit=40")
events = audit.get("events", audit if isinstance(audit, list) else [])
restored = [e for e in events if e.get("entity_type") == "field_hidden"
            and "Restored" in (e.get("summary") or "")]
ck("restoring writes its own event", len(restored) > 0,
   str(len(restored)))

# Setting it to what it already is should not fill the trail with noise.
before = len([e for e in events if e.get("entity_type") == "field_hidden"])
rq(boss, "PUT", "/api/field-visibility",
   {"entity": "data_flow", "column": "legal_authority",
    "hidden": was("data_flow", "legal_authority")})
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
   {"entity": "system", "column": "notes", "hidden": was("system", "notes")})

# ------------------------------------------------- left as it was found
# This suite changes real settings, so it owes the database its original state
# back — including any field its owner had hidden before it ran.
st, cat = rq(boss, "GET", "/api/field-catalog")
ended_hidden = {(f["entity"], f["column"]) for f in cat["fields"] if f["hidden"]}
ck("every hidden field is exactly as it was before this suite ran",
   ended_hidden == ORIGINALLY_HIDDEN,
   f"was {sorted(ORIGINALLY_HIDDEN)}, now {sorted(ended_hidden)}")

print(f"\n  {len(P)} passed, {len(F)} failed")
for f in F:
    print("  FAILED:", f)
sys.exit(1 if F else 0)
