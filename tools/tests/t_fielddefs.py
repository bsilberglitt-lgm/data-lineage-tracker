"""0.5.0 — field definitions.

    python3 tools/tests/t_fielddefs.py 8900

The thing most worth protecting here is the **label resolution**. A definition
is stored against a database column, but the browser looks it up by the label
the component happened to be given, and those labels differ by view:

    system.encryption_at_rest   edit view "Encrypt at Rest"
                                read view "Encryption at Rest"
                                add modal "Encryption at Rest"

Seventeen of the seventy-seven fields have more than one spelling. If the
server emitted a definition under only one of them, it would appear in the edit
view and silently vanish in the read view — a bug that looks like the feature
half-working and would be very hard to attribute. So: write once, and assert it
comes back under *every* spelling the catalogue knows.

Also covered: only a SuperAdmin may write, everyone may read, unknown fields
are refused rather than stored, an empty definition deletes rather than storing
a blank, and the change is audited.
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


def seeded_definitions():
    """`SEED_FIELD_DEFINITIONS` out of app.py, **without importing app.py.**

    This used to be `import app as _app`, which works only where the app's
    dependencies are installed. On Ben's Mac they are not — they live in the
    Docker image — so the import died with `ModuleNotFoundError: psycopg_pool`
    four checks into the suite. A test that reads one list should not need the
    whole application, its database driver, or a running connection pool.

    So the list is parsed. `ast.literal_eval` on the assignment's value is safe
    (no code is executed) and needs nothing installed.
    """
    import ast
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "..", "..", "app.py"),
                 os.path.join(os.getcwd(), "app.py"),
                 os.path.join(here, "app.py")):
        if os.path.exists(cand):
            tree = ast.parse(open(cand).read())
            for node in tree.body:
                if (isinstance(node, ast.Assign)
                        and any(getattr(t, "id", None) == "SEED_FIELD_DEFINITIONS"
                                for t in node.targets)):
                    return ast.literal_eval(node.value)
            raise SystemExit("SEED_FIELD_DEFINITIONS not found in " + cand)
    raise SystemExit("could not find app.py — run this from the project root")


def rq(s, m, p, b=None):
    d = json.dumps(b).encode() if b is not None else None
    r = urllib.request.Request(B + p, data=d, method=m)
    if d:
        r.add_header("Content-Type", "application/json")
    if s["t"] and m != "GET":
        r.add_header("X-CSRF-Token", s["t"])
    try:
        with s["op"].open(r, timeout=60) as x:
            return x.status, json.loads(x.read().decode() or "null")
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw[:200]


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

print("-- the catalogue --")
st, cat = rq(boss, "GET", "/api/field-catalog")
ck("the catalogue is served", st == 200 and len(cat.get("fields", [])) > 0, str(st))
ck("it matches the generated catalogue exactly",
   len(cat["fields"]) == len(field_catalog.FIELDS),
   f"{len(cat['fields'])} vs {len(field_catalog.FIELDS)}")
ck("every entry carries a section, so the picker can group it",
   all(f["section"] for f in cat["fields"]))
ck("record types are named for people, not for the database",
   cat["entity_labels"]["data_flow"] == "Data Flow", str(cat.get("entity_labels")))
# The app ships with a small number of definitions for fields whose name would
# otherwise be misread as a different field — see SEED_FIELD_DEFINITIONS in
# app.py. Those must be present.
#
# What this does NOT assert is that nothing else is defined. It used to, and
# that was the same mistake `t_fieldhide` was fixed for: **a suite borrows the
# database, it does not own it.** Any definition somebody wrote while using the
# app — or that another suite in the same run left behind, which is how this
# surfaced — was reported as a failure of this feature. `t_fieldhide` writes a
# definition to prove hiding and defining are independent, so running the two
# in either order broke one of them.
#
# The invariant the original check was reaching for is that a definition
# appears only where one was written, and that is what the rest of this suite
# proves directly: it writes one, reads it back, overrides it, clears it, and
# checks the field is blank again.
SEEDED = {(e, c) for e, c, _ in seeded_definitions()}
ck("what the app ships with is present",
   all(any(f["entity"] == e and f["column"] == c and f["definition"]
           for f in cat["fields"]) for e, c in SEEDED),
   str(sorted(SEEDED)))
extra = [f"{f['entity']}.{f['column']}" for f in cat["fields"]
         if f["definition"] and (f["entity"], f["column"]) not in SEEDED]
if extra:
    print(f"   (note: {len(extra)} field(s) already carry a definition on this "
          f"database: {', '.join(extra[:5])}"
          f"{' …' if len(extra) > 5 else ''}. Not a failure.)")

print("\n-- one definition, found under every spelling --")
multi = [f for f in field_catalog.FIELDS
         if len({f["label"], f.get("read_label"), f.get("modal_label")} - {None}) > 1]
ck("the catalogue has fields with more than one spelling", len(multi) > 0,
   "if this fails the next checks prove nothing")
target = next(f for f in multi if f["entity"] == "system")
spellings = {target["label"], target.get("read_label"), target.get("modal_label")} - {None}
TEXT = "Whether the data is encrypted while stored on disk, as opposed to in transit."
st, r = rq(boss, "PUT", "/api/field-catalog",
           {"entity": target["entity"], "column": target["column"], "definition": TEXT})
ck("a definition can be written", st == 200, f"{st} {str(r)[:90]}")

st, d = rq(boss, "GET", "/api/field-definitions")
defs = d["definitions"]
for sp in sorted(spellings):
    key = f"{target['entity']}|{sp}"
    ck(f"found under '{sp}'", defs.get(key) == TEXT,
       f"missing {key} — it would appear in one view and not another")
ck("and under no other record type",
   not any(k.endswith("|" + target["label"]) and not k.startswith(target["entity"] + "|")
           for k in defs))

print("\n-- the same label on two record types stays separate --")
both = [f for f in field_catalog.FIELDS if f["label"] == "Classification"]
ck("'Classification' exists on more than one record type", len(both) > 1,
   str([f["entity"] for f in both]))
if len(both) > 1:
    a, c = both[0], both[1]
    rq(boss, "PUT", "/api/field-catalog",
       {"entity": a["entity"], "column": a["column"], "definition": "For a " + a["entity"]})
    st, d = rq(boss, "GET", "/api/field-definitions")
    ck("defining it on one does not define it on the other",
       d["definitions"].get(f"{a['entity']}|Classification") is not None and
       d["definitions"].get(f"{c['entity']}|Classification") is None,
       str({k: v for k, v in d["definitions"].items() if "Classification" in k}))

print("\n-- editing and clearing --")
st, r = rq(boss, "PUT", "/api/field-catalog",
           {"entity": target["entity"], "column": target["column"],
            "definition": "A shorter answer."})
st, d = rq(boss, "GET", "/api/field-definitions")
ck("rewording replaces rather than appends",
   d["definitions"][f"{target['entity']}|{target['label']}"] == "A shorter answer.")
st, r = rq(boss, "PUT", "/api/field-catalog",
           {"entity": target["entity"], "column": target["column"], "definition": "   "})
ck("an empty definition is accepted", st == 200, f"{st} {str(r)[:90]}")
st, d = rq(boss, "GET", "/api/field-definitions")
ck("and removes it rather than storing a blank",
   not any(k.startswith(target["entity"] + "|") and
           k.endswith(target["label"]) for k in d["definitions"]),
   "an empty bubble is worse than no bubble")

print("\n-- what is refused --")
st, r = rq(boss, "PUT", "/api/field-catalog",
           {"entity": "system", "column": "no_such_column", "definition": "x"})
ck("an unknown column is refused", st == 400, f"{st} {str(r)[:90]}")
st, r = rq(boss, "PUT", "/api/field-catalog",
           {"entity": "preference", "column": "app_title", "definition": "x"})
ck("a record type outside the catalogue is refused", st == 400, f"{st} {str(r)[:90]}")
st, r = rq(boss, "PUT", "/api/field-catalog",
           {"entity": target["entity"], "column": target["column"],
            "definition": "x" * 2000})
ck("an over-long definition is refused", st == 400, f"{st} {str(r)[:90]}")

print("\n-- who may do what --")
email, pw = "defs.reader@maine.gov", "Juniper-Vault-37#p"
st, existing = rq(boss, "GET", "/api/users")
found = next((u for u in existing if u["email"] == email), None) \
    if isinstance(existing, list) else None
if found:
    rq(boss, "PUT", f"/api/users/{found['user_id']}",
       {"is_active": 1, "password": pw, "role": "Editor"})
else:
    rq(boss, "POST", "/api/users", {"email": email, "first_name": "Robin",
                                    "last_name": "Hale", "role": "Editor",
                                    "password": pw})
rq(boss, "PUT", "/api/field-catalog",
   {"entity": target["entity"], "column": target["column"], "definition": TEXT})

ed = S()
st, vb = rq(ed, "POST", "/api/auth/login", {"email": email, "password": pw})
if st == 200:
    ed["t"] = vb["csrf_token"]
    if vb["user"].get("must_change_password"):
        rq(ed, "POST", "/api/auth/change-password",
           {"current_password": pw, "new_password": "Cobalt-Meadow-84#r"})
    st, d = rq(ed, "GET", "/api/field-definitions")
    ck("an Editor can read definitions, since they describe fields they can see",
       st == 200 and len(d.get("definitions", {})) > 0, f"{st}")
    st, r = rq(ed, "GET", "/api/field-catalog")
    ck("but cannot see the editor's catalogue", st == 403, str(st))
    st, r = rq(ed, "PUT", "/api/field-catalog",
               {"entity": target["entity"], "column": target["column"],
                "definition": "sneaky"})
    ck("and cannot write one", st == 403, f"{st} {str(r)[:80]}")
    st, d = rq(boss, "GET", "/api/field-definitions")
    ck("the definition is unchanged after that attempt",
       d["definitions"][f"{target['entity']}|{target['label']}"] == TEXT)
else:
    ck("an Editor account could be created", False, f"login {st}")

anon = S()
st, r = rq(anon, "GET", "/api/field-definitions")
ck("a stranger gets nothing", st in (401, 403), str(st))

print("\n-- the audit trail --")
st, ev = rq(boss, "GET", "/api/audit?entity_type=field_definition")
rows = ev.get("events", ev) if isinstance(ev, dict) else ev
summaries = [e.get("summary", "") for e in rows] if isinstance(rows, list) else []
ck("writing a definition is audited", len(summaries) >= 1, str(summaries[:2]))
ck("the summary names the field in human terms",
   any("System" in s and target["label"] in s for s in summaries),
   str(summaries[:3]))
ck("removing one is audited too", any("Removed" in s for s in summaries),
   str(summaries[:4]))

print("\n-- the fields the generator used to miss --")
# Data Steward and Owner are drawn by ContributorSelect / ContributorField
# rather than EditField, so the first version of the generator skipped them
# silently: six fields absent from the catalogue with no error anywhere.
by_col = {(f["entity"], f["column"]) for f in field_catalog.FIELDS}
for ent, col in [("system", "data_steward_id"), ("data_flow", "data_steward_id"),
                 ("data_flow", "owner_id"), ("data_element", "data_owner_id")]:
    ck(f"{ent}.{col} is in the catalogue", (ent, col) in by_col,
       "rendered by a component the generator has to know about")
st, cat = rq(boss, "GET", "/api/field-catalog")
steward = [f for f in cat["fields"] if f["column"] == "data_steward_id"]
ck("Data Steward appears on three record types", len(steward) == 3,
   str([f["entity"] for f in steward]))
ck("and each knows about the others",
   all(len(f["shared_with"]) == 2 for f in steward),
   str([(f["entity"], f["shared_with"]) for f in steward]))

print("\n-- one shared definition, reaching every record type --")
SHARED_TEXT = "The person accountable for this data's quality and correct use."
st, r = rq(boss, "PUT", "/api/field-catalog",
           {"entity": "*", "column": "data_steward_id", "definition": SHARED_TEXT})
ck("a shared definition can be written", st == 200, f"{st} {str(r)[:90]}")
st, d = rq(boss, "GET", "/api/field-definitions")
for ent in ("system", "data_flow", "data_element"):
    ck(f"it reaches {ent}", d["definitions"].get(f"{ent}|Data Steward") == SHARED_TEXT,
       "one edit should cover all three")
st, cat = rq(boss, "GET", "/api/field-catalog")
steward = [f for f in cat["fields"] if f["column"] == "data_steward_id"]
ck("the catalogue reports it as inherited rather than written here",
   all(f["inherited"] and not f["own_definition"] for f in steward),
   str([(f["entity"], f["inherited"], f["own_definition"]) for f in steward]))

print("\n-- rewording the shared one updates all of them --")
AGAIN = "Accountable for the quality and correct use of this data."
rq(boss, "PUT", "/api/field-catalog",
   {"entity": "*", "column": "data_steward_id", "definition": AGAIN})
st, d = rq(boss, "GET", "/api/field-definitions")
ck("every record type picks up the new wording",
   all(d["definitions"].get(f"{e}|Data Steward") == AGAIN
       for e in ("system", "data_flow", "data_element")),
   "this is the point of sharing")

print("\n-- a record type can still override --")
OWN = "On a data flow, the steward of the data in transit."
rq(boss, "PUT", "/api/field-catalog",
   {"entity": "data_flow", "column": "data_steward_id", "definition": OWN})
st, d = rq(boss, "GET", "/api/field-definitions")
ck("the override wins for that record type",
   d["definitions"].get("data_flow|Data Steward") == OWN, 
   str(d["definitions"].get("data_flow|Data Steward"))[:60])
ck("and the others still show the shared text",
   d["definitions"].get("system|Data Steward") == AGAIN and
   d["definitions"].get("data_element|Data Steward") == AGAIN,
   "an override must not disturb its neighbours")
st, cat = rq(boss, "GET", "/api/field-catalog")
flow = next(f for f in cat["fields"]
            if f["entity"] == "data_flow" and f["column"] == "data_steward_id")
ck("the catalogue shows it as its own, not inherited",
   flow["own_definition"] == OWN and not flow["inherited"],
   str((flow["own_definition"][:30], flow["inherited"])))

print("\n-- removing the override falls back to shared --")
rq(boss, "PUT", "/api/field-catalog",
   {"entity": "data_flow", "column": "data_steward_id", "definition": ""})
st, d = rq(boss, "GET", "/api/field-definitions")
ck("it returns to the shared wording rather than going blank",
   d["definitions"].get("data_flow|Data Steward") == AGAIN,
   "clearing an override should reveal what it was hiding")

print("\n-- sharing is refused where it makes no sense --")
st, r = rq(boss, "PUT", "/api/field-catalog",
           {"entity": "*", "column": "vendor", "definition": "x"})
ck("a column on only one record type cannot be shared", st == 400,
   f"{st} {str(r)[:100]}")

print("\n-- the shared definition is audited as shared --")
st, ev = rq(boss, "GET", "/api/audit?entity_type=field_definition")
rows = ev.get("events", ev) if isinstance(ev, dict) else ev
sums = [e.get("summary", "") for e in rows] if isinstance(rows, list) else []
ck("the audit says which record types it covers",
   any("shared across" in x and "Data Steward" in x for x in sums), str(sums[:3]))

print(f"\n  {len(P)} passed, {len(F)} failed")
if F:
    print("  FAILURES:", F)
    sys.exit(1)
