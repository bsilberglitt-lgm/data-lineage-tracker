"""0.4.4 — retiring in a scenario, carrying collaborators over, editing a resource.

    python3 tools/tests/t_044.py 8900

Three of these were capabilities the server already had and nothing ever
called, which is a category of bug worth naming: the code is right, the tests
pass, and the feature does not exist as far as anyone using it is concerned.

What this pins down:

  1. **Retiring, and un-retiring.** `mode=remove` existed since 0.3.0 with no
     caller. It was also one-way — `mark_modified` deliberately leaves
     "removed" alone — so a mis-click could not be undone without rebuilding
     the scenario. `mode=restore` is new, and both now route through one
     function in scenarios.py so they cannot disagree.
  2. **The Proposed checkbox must not clobber a retired record.** It wrote
     change_state unconditionally, so saving a retired system with the box
     clear quietly un-retired it, and with the box checked claimed a live
     system had been invented in the scenario.
  3. **copy_members.** The modal has always sent it; nothing read it. Carried
     people must arrive with their original roles, the source's owner must
     arrive as an editor rather than a second owner, and someone both carried
     over and explicitly invited must not break the primary key.
  4. **Editing a resource.** PUT has always accepted label, description and
     URL. Nothing called it, so fixing a typo meant delete and re-add, losing
     the version history.
"""
import io, json, sys, urllib.request, urllib.error, http.cookiejar, uuid
import os


def rq(s, m, p, b=None, sc=None):
    if sc is not None:
        p += ("&" if "?" in p else "?") + f"scenario={sc}"
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


def upload(s, url, filename, content, sc=None):
    if sc is not None:
        url += ("&" if "?" in url else "?") + f"scenario={sc}"
    bnd = "----b" + uuid.uuid4().hex
    body = io.BytesIO()
    body.write(f"--{bnd}\r\nContent-Disposition: form-data; name=\"file\"; "
               f"filename=\"{filename}\"\r\nContent-Type: text/plain\r\n\r\n".encode())
    body.write(content)
    body.write(f"\r\n--{bnd}--\r\n".encode())
    r = urllib.request.Request(B + url, data=body.getvalue(), method="POST")
    r.add_header("Content-Type", f"multipart/form-data; boundary={bnd}")
    if s["t"]:
        r.add_header("X-CSRF-Token", s["t"])
    try:
        with s["op"].open(r, timeout=60) as x:
            return x.status, json.loads(x.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:200]


def members_of(s, sid):
    """Members come from the scenario payload, not from a GET on /members —
    that route only accepts POST/PUT/DELETE. An earlier version of this test
    asked for it anyway and silently parsed the error body as data."""
    st, rows = rq(s, "GET", "/api/scenarios")
    rows = rows.get("scenarios", rows) if isinstance(rows, dict) else rows
    one = next((x for x in rows if x["scenario_id"] == sid), None)
    return one.get("members", []) if one else []


# Credentials and signing in come from _client.py — no hardcoded password, and
# nothing here changes one. This suite keeps its own `rq`, which takes an extra
# `sc=` scenario argument, so it borrows just the sign-in.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _client import sign_in_with, session as S, BASE as B  # noqa: E402

P, F = [], []


def ck(l, c, d=""):
    (P if c else F).append(l)
    print(f"  {'PASS' if c else 'FAIL'}  {l}" + (f"   [{d}]" if not c and d else ""))


boss = sign_in_with(S(), rq)

# Two colleagues, so membership can actually be carried.
people = []
for first, last, email in [("Dana", "Reed", "dana.reed@example.gov"),
                           ("Mel", "Okafor", "mel.okafor@example.gov")]:
    st, existing = rq(boss, "GET", "/api/users")
    found = next((u for u in existing if u["email"] == email), None) \
        if isinstance(existing, list) else None
    if found:
        people.append(found["user_id"])
        continue
    st, made = rq(boss, "POST", "/api/users",
                  {"email": email, "first_name": first, "last_name": last,
                   "role": "Editor", "password": "Harbor-Lantern-61#t"})
    people.append(made.get("user_id") if isinstance(made, dict) else None)

print("-- retiring something that exists today --")
st, scn = rq(boss, "POST", "/api/scenarios",
             {"name": "Retire test", "start_from": "live"})
sid = scn["scenario_id"]
st, sys_rows = rq(boss, "GET", "/api/systems", None, sc=sid)
target = sys_rows[0]["system_id"]

st, r = rq(boss, "DELETE", f"/api/systems/{target}?mode=remove", None, sc=sid)
ck("a copied system can be retired", st == 200 and r.get("change_state") == "removed",
   f"{st} {str(r)[:90]}")
st, rows = rq(boss, "GET", "/api/systems", None, sc=sid)
row = next(x for x in rows if x["system_id"] == target)
ck("it is still there, marked removed", row["change_state"] == "removed")
st, live = rq(boss, "GET", "/api/systems")
ck("Current Architecture is untouched",
   all(x["change_state"] in (None, "unchanged") for x in live))

print("\n-- and un-retiring it, which was impossible before --")
st, r = rq(boss, "DELETE", f"/api/systems/{target}?mode=restore", None, sc=sid)
ck("it can be brought back", st == 200 and r.get("change_state") == "unchanged",
   f"{st} {str(r)[:90]}")
st, r = rq(boss, "DELETE", f"/api/systems/{target}?mode=restore", None, sc=sid)
ck("restoring something not retired is refused with a reason", st == 400,
   f"{st} {str(r)[:90]}")

print("\n-- something the scenario invented cannot be retired --")
st, made = rq(boss, "POST", "/api/systems",
              {"system_id": "SYS-NEW1", "name": "Invented here",
               "system_type": "Data Store", "status": "Active", "proposed": True},
              sc=sid)
ck("a proposed system was created", st in (200, 201), f"{st} {str(made)[:90]}")
new_id = made.get("system_id") if isinstance(made, dict) else None
st, r = rq(boss, "DELETE", f"/api/systems/{new_id}?mode=remove", None, sc=sid)
ck("retiring it is refused, pointing at Delete instead",
   st == 400 and "delete" in str(r).lower(), f"{st} {str(r)[:110]}")

print("\n-- the Proposed checkbox must not clobber a retired record --")
rq(boss, "DELETE", f"/api/systems/{target}?mode=remove", None, sc=sid)
st, _ = rq(boss, "PUT", f"/api/systems/{target}", {"proposed": False}, sc=sid)
st, rows = rq(boss, "GET", "/api/systems", None, sc=sid)
row = next(x for x in rows if x["system_id"] == target)
ck("saving with the box clear does not un-retire it", row["change_state"] == "removed",
   f"became {row['change_state']}")
st, _ = rq(boss, "PUT", f"/api/systems/{target}", {"proposed": True}, sc=sid)
st, rows = rq(boss, "GET", "/api/systems", None, sc=sid)
row = next(x for x in rows if x["system_id"] == target)
ck("and checking it does not claim a live system was invented here",
   row["change_state"] == "removed", f"became {row['change_state']}")

print("\n-- retiring a data flow --")
st, flows = rq(boss, "GET", "/api/flows", None, sc=sid)
fid = flows[0]["flow_id"]
st, r = rq(boss, "DELETE", f"/api/flows/{fid}?mode=remove", None, sc=sid)
ck("a flow can be retired", st == 200 and r.get("change_state") == "removed",
   f"{st} {str(r)[:90]}")
st, r = rq(boss, "DELETE", f"/api/flows/{fid}?mode=restore", None, sc=sid)
ck("and brought back", st == 200 and r.get("change_state") == "unchanged")

print("\n-- retiring does not work outside a scenario --")
st, live = rq(boss, "GET", "/api/systems")
st, r = rq(boss, "DELETE", f"/api/systems/{live[0]['system_id']}?mode=remove")
ck("on Current Architecture the mode is ignored, not obeyed", st != 200 or
   (isinstance(r, dict) and r.get("change_state") != "removed"), f"{st} {str(r)[:90]}")

print("\n-- carrying collaborators to a new scenario --")
st, _ = rq(boss, "POST", f"/api/scenarios/{sid}/members",
           {"user_id": people[0], "role": "editor"})
st, _ = rq(boss, "POST", f"/api/scenarios/{sid}/members",
           {"user_id": people[1], "role": "viewer"})
st, branched = rq(boss, "POST", "/api/scenarios",
                  {"name": "Branch with people", "start_from": "scenario",
                   "source_scenario_id": sid, "copy_members": True})
ck("the branch was created", st in (200, 201), f"{st} {str(branched)[:90]}")
bid = branched["scenario_id"]
ck("it reports how many were carried", branched.get("copied_members") == 2,
   f"copied_members={branched.get('copied_members')}")
rows = members_of(boss, bid)
by_id = {m["user_id"]: m["role"] for m in rows}
ck("the editor arrived as an editor", by_id.get(people[0]) == "editor", str(by_id))
ck("the viewer arrived as a viewer", by_id.get(people[1]) == "viewer", str(by_id))
ck("and the creator owns it", any(m["role"] == "owner" for m in rows))
ck("exactly one owner", sum(1 for m in rows if m["role"] == "owner") == 1,
   str([m["role"] for m in rows]))

print("\n-- without the box, nobody is carried --")
st, plain = rq(boss, "POST", "/api/scenarios",
               {"name": "Branch without people", "start_from": "scenario",
                "source_scenario_id": sid, "copy_members": False})
rows = members_of(boss, plain["scenario_id"])
ck("only the owner is a member", len(rows) == 1, str([m["role"] for m in rows]))

print("\n-- carried over and invited at once must not collide --")
st, both = rq(boss, "POST", "/api/scenarios",
              {"name": "Branch with overlap", "start_from": "scenario",
               "source_scenario_id": sid, "copy_members": True,
               "invite": [{"user_id": people[0], "role": "viewer"}]})
ck("creating it succeeds rather than failing on a duplicate key",
   st in (200, 201), f"{st} {str(both)[:140]}")
if st in (200, 201):
    rows = members_of(boss, both["scenario_id"])
    ids = [m["user_id"] for m in rows]
    ck("the person appears once, not twice", ids.count(people[0]) == 1, str(ids))

print("\n-- editing a resource instead of deleting and re-adding it --")
st, live = rq(boss, "GET", "/api/systems")
host = live[0]["system_id"]
st, res = rq(boss, "POST", f"/api/resources/system/{host}",
             {"label": "Origianl titel", "kind": "link",
              "url": "https://example.gov/old", "description": "first draft"})
rid = [r for r in res if r["label"] == "Origianl titel"][0]["resource_id"]
st, r = rq(boss, "PUT", f"/api/resources/{rid}",
           {"label": "Original title", "description": "corrected",
            "url": "https://example.gov/new"})
ck("label, description and URL can all be corrected", st == 200, f"{st} {str(r)[:110]}")
st, rows = rq(boss, "GET", f"/api/resources/system/{host}")
got = next(x for x in rows if x["resource_id"] == rid)
ck("the typo is fixed", got["label"] == "Original title", got["label"])
ck("the description is updated", got["description"] == "corrected")
ck("the URL is updated", got["url"] == "https://example.gov/new", str(got.get("url")))
ck("and the version history survived the edit", got.get("version_count", 0) >= 1,
   "the whole point of editing rather than re-adding")

st, r = rq(boss, "PUT", f"/api/resources/{rid}", {"url": "not-a-url"})
ck("a malformed URL is still refused", st == 400, f"{st} {str(r)[:90]}")
st, r = rq(boss, "PUT", f"/api/resources/{rid}", {"label": "   "})
ck("an empty label is still refused", st == 400, f"{st} {str(r)[:90]}")

print(f"\n  {len(P)} passed, {len(F)} failed")
if F:
    print("  FAILURES:", F)
    sys.exit(1)
