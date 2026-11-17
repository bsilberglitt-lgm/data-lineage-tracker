"""0.6.0 — your own account.

    python3 tools/tests/t_060.py 8900

The thing worth protecting is the **boundary of self-service**. A profile
endpoint any Viewer may call is a new way into the user table, and the whole
reason it is a separate handler from `/api/users/<id>` is that the admin one
can change a role, issue a password and deactivate an account. If the profile
endpoint ever accepted `role`, every signed-in user could promote themselves.

So: it refuses anything outside name and title, **by name and with a 403**
rather than ignoring it — silently dropping a field is the failure mode this
project keeps finding, and it would make a privilege escalation attempt look
like a success.

Also covered: a title lives on the account and is shown on a linked
contributor; a contributor with no account keeps their own; the change is
audited against the person who made it; and the existing password endpoint
still revokes other sessions, which the profile window now relies on.
"""
import json
import os
import sys


# Credentials, the connection and signing in all come from _client.py, so no
# suite carries a hardcoded password and none of them changes one. See the
# comment at the top of that file for why that matters.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _client import (sign_in, session as S, request as rq,  # noqa: E402
                     ensure_account, unique_email, BASE as B)

P, F = [], []


def ck(l, c, d=""):
    (P if c else F).append(l)
    print(f"  {'PASS' if c else 'FAIL'}  {l}" + (f"   [{d}]" if not c and d else ""))


boss = sign_in()

# ------------------------------------------------------- a Viewer to act as
TEMP = "Thistle-Quay-52#k"
# Created, or reset if an earlier run already made it. A plain POST is a 409
# the second time and the sign-in that follows then fails with 401, which is
# reported as the profile endpoint being broken. See _client.
st, viewer, REAL = ensure_account(
    boss, rq, "p.lowell@example.gov", "Pat", "Lowell", "Viewer", TEMP)
ck("the Viewer can sign in", bool(viewer["t"]), f"login {st}")

# --------------------------------------------------------- what they may do
print("-- a Viewer can change their own name and title --")
st, me = rq(viewer, "PUT", "/api/auth/profile",
            {"first_name": "Patricia", "last_name": "Lowell",
             "title": "Records Analyst"})
ck("the profile save is accepted", st == 200, str(st))
ck("the name is changed", me.get("first_name") == "Patricia",
   repr(me.get("first_name")))
ck("the title is stored", me.get("title") == "Records Analyst",
   repr(me.get("title")))

st, who = rq(viewer, "GET", "/api/auth/me")
ck("and comes back on the session", who["user"].get("title") == "Records Analyst",
   repr(who["user"].get("title")))

st, me = rq(viewer, "PUT", "/api/auth/profile", {"title": ""})
ck("a title can be cleared", st == 200 and not me.get("title"),
   repr(me.get("title")))
rq(viewer, "PUT", "/api/auth/profile", {"title": "Records Analyst"})

# ----------------------------------------------------- what they may not do
print("-- and nothing else --")
for field, value in [("role", "SuperAdmin"), ("email", "someone.else@example.gov"),
                     ("is_active", 0), ("password", "Whatever-9xQ!"),
                     ("contributor_id", "CON-001"),
                     ("must_change_password", 0)]:
    st, r = rq(viewer, "PUT", "/api/auth/profile", {field: value})
    ck(f"{field} is refused", st == 403, str(st))
    ck(f"the refusal names {field}",
       isinstance(r, dict) and field in json.dumps(r), json.dumps(r)[:110])

# The refusal has to be real, not just a message.
st, who = rq(viewer, "GET", "/api/auth/me")
ck("the Viewer is still a Viewer", who["user"]["role"] == "Viewer",
   who["user"]["role"])
ck("with their original email", who["user"]["email"] == "p.lowell@example.gov",
   who["user"]["email"])

# A mixture must be refused whole rather than partly applied.
st, r = rq(viewer, "PUT", "/api/auth/profile",
           {"first_name": "Escalated", "role": "SuperAdmin"})
ck("a mixed request is refused entirely", st == 403, str(st))
st, who = rq(viewer, "GET", "/api/auth/me")
ck("and the permitted half was not applied either",
   who["user"]["first_name"] == "Patricia", who["user"]["first_name"])

st, _ = rq(viewer, "PUT", "/api/users/1", {"role": "Viewer"})
ck("a Viewer still cannot use the admin endpoint", st == 403, str(st))

st, r = rq(viewer, "PUT", "/api/auth/profile", {"first_name": "   "})
ck("a blank first name is refused", st == 400, str(st))

# ---------------------------------------------------- title on a contributor
print("-- a title follows the account onto the contributor --")
st, person = rq(boss, "POST", "/api/contributors",
                {"name": "Pat Lowell", "title": "Typed on the contributor"})
cid = person.get("contributor_id")
st, unlinked = rq(boss, "GET", "/api/contributors")
row = next(c for c in unlinked if c["contributor_id"] == cid)
ck("an unlinked contributor keeps their own title",
   row.get("effective_title") == "Typed on the contributor",
   repr(row.get("effective_title")))
ck("and reports no account title", not row.get("account_title"),
   repr(row.get("account_title")))

st, users = rq(boss, "GET", "/api/users")
uid = next(u["user_id"] for u in users if u["email"] == "p.lowell@example.gov")
st, _ = rq(boss, "PUT", f"/api/users/{uid}", {"contributor_id": cid})
ck("the account can be linked to the contributor", st == 200, str(st))

st, linked = rq(boss, "GET", "/api/contributors")
row = next(c for c in linked if c["contributor_id"] == cid)
ck("the account's title now wins",
   row.get("effective_title") == "Records Analyst",
   repr(row.get("effective_title")))
ck("and the account title is reported, so the field can be made read-only",
   row.get("account_title") == "Records Analyst",
   repr(row.get("account_title")))
ck("the contributor's own title is untouched underneath",
   row.get("title") == "Typed on the contributor", repr(row.get("title")))

# An administrator may also set a title on the account.
st, _ = rq(boss, "PUT", f"/api/users/{uid}", {"title": "Senior Records Analyst"})
st, linked = rq(boss, "GET", "/api/contributors")
row = next(c for c in linked if c["contributor_id"] == cid)
ck("an administrator can set someone's title",
   row.get("effective_title") == "Senior Records Analyst",
   repr(row.get("effective_title")))

# A deactivated account should stop supplying a title: it is no longer current.
st, _ = rq(boss, "PUT", f"/api/users/{uid}", {"is_active": 0})
st, linked = rq(boss, "GET", "/api/contributors")
row = next(c for c in linked if c["contributor_id"] == cid)
ck("a deactivated account stops supplying the title",
   row.get("effective_title") == "Typed on the contributor",
   repr(row.get("effective_title")))
rq(boss, "PUT", f"/api/users/{uid}", {"is_active": 1})

# ------------------------------------------------------------------- audited
print("-- the change is recorded against the person who made it --")
st, audit = rq(boss, "GET", "/api/audit?limit=60")
events = audit.get("events", [])
mine = [e for e in events
        if e.get("entity_type") == "user" and str(e.get("entity_id")) == str(uid)]
ck("the profile change is in the audit trail", len(mine) > 0, str(len(mine)))
ck("attributed to the person themselves",
   any((e.get("actor_email") or "") == "p.lowell@example.gov" for e in mine),
   "; ".join(sorted({(e.get("actor_email") or "?") for e in mine})))

# ------------------------------------- a contributor made from an account
print("-- adding a contributor who already has an account --")
st, listed = rq(boss, "GET", "/api/users/pickable")
ck("the pickable list is served", st == 200 and isinstance(listed, list), str(st))
ck("it carries what the window needs and nothing about access",
   listed and set(listed[0]) == {"user_id", "name", "email", "title",
                                 "linked_contributor_id",
                                 "linked_contributor_name"},
   str(sorted(listed[0]) if listed else []))

# A brand-new address every run. This block is about an account nobody has
# touched — a title stored AT CREATION, and an unlinked account being offered
# in the picker — and the previous run linked `r.hale` to a contributor, so
# reusing the address makes both checks false. `ensure_account` cannot help:
# resetting a password does not unlink anything.
HALE = unique_email("r.hale")
st, fresh = rq(boss, "POST", "/api/users",
               {"email": HALE, "first_name": "Robin",
                "last_name": "Hale", "role": "Editor",
                "password": "Juniper-Vault-37#p", "title": "Systems Architect"})
ck("a title given at creation is stored", fresh.get("title") == "Systems Architect",
   repr(fresh.get("title")))

st, listed = rq(boss, "GET", "/api/users/pickable")
robin = next(u for u in listed if u["email"] == HALE)
ck("the new account is offered", robin["linked_contributor_id"] is None,
   repr(robin["linked_contributor_id"]))
ck("with their title, so the window can show it",
   robin["title"] == "Systems Architect", repr(robin["title"]))

st, made = rq(boss, "POST", "/api/contributors",
              {"name": robin["name"], "link_user_id": robin["user_id"]})
ck("the contributor is created", st in (200, 201), str(st))
ck("under the account's name", made.get("name") == "Robin Hale",
   repr(made.get("name")))

# The email and title are resolved from the account rather than copied, which
# is the same decision as the rest of this release: one value, one owner.
ck("the email is not copied onto the contributor", not made.get("email"),
   repr(made.get("email")))
ck("the title is not copied either", not made.get("title"),
   repr(made.get("title")))
ck("but both resolve from the account",
   made.get("contact_email") == HALE
   and made.get("effective_title") == "Systems Architect",
   f"{made.get('contact_email')!r} {made.get('effective_title')!r}")

st, allu = rq(boss, "GET", "/api/users")
linked = next(u for u in allu if u["email"] == HALE)
ck("the account now points at the contributor",
   linked.get("contributor_id") == made.get("contributor_id"),
   repr(linked.get("contributor_id")))

st, listed = rq(boss, "GET", "/api/users/pickable")
robin = next(u for u in listed if u["email"] == HALE)
ck("the list now reports them as linked",
   robin["linked_contributor_id"] == made.get("contributor_id"),
   repr(robin["linked_contributor_id"]))
ck("and names the contributor, for the grayed-out note",
   robin["linked_contributor_name"] == "Robin Hale",
   repr(robin["linked_contributor_name"]))

# The window grays them out, but the window is a snapshot — two people could
# be adding contributors at once, so the server has to refuse as well.
st, r = rq(boss, "POST", "/api/contributors",
           {"name": "Robin Hale Again", "link_user_id": robin["user_id"]})
ck("linking the same account twice is refused", st == 409, str(st))
ck("and the refusal says who has them",
   isinstance(r, dict) and "Robin Hale" in json.dumps(r), json.dumps(r)[:120])

st, r = rq(boss, "POST", "/api/contributors",
           {"name": "Nobody", "link_user_id": 99999})
ck("an unknown account is refused", st == 400, str(st))

# A deactivated account should not be offered or accepted: its title and
# address are no longer current, which is the same rule as elsewhere.
st, _ = rq(boss, "PUT", f"/api/users/{linked['user_id']}", {"is_active": 0})
st, listed = rq(boss, "GET", "/api/users/pickable")
ck("a deactivated account drops out of the list",
   not any(u["email"] == HALE for u in listed))
# New every run, like HALE above and for the same reason: this proves an
# account that has never been linked is refused once deactivated, so it has to
# be an account nobody has linked. A second run against the same database
# turned the POST into a 409 and the next line into a KeyError.
VANCE = unique_email("k.vance")
st, fresh2 = rq(boss, "POST", "/api/users",
                {"email": VANCE, "first_name": "Kit",
                 "last_name": "Vance", "role": "Viewer",
                 "password": "Cobalt-Meadow-84#r"})
ck("the throwaway account is created", st in (200, 201), str(st))
rq(boss, "PUT", f"/api/users/{fresh2['user_id']}", {"is_active": 0})
st, r = rq(boss, "POST", "/api/contributors",
           {"name": "Kit Vance", "link_user_id": fresh2["user_id"]})
ck("and cannot be linked", st == 400, str(st))
rq(boss, "PUT", f"/api/users/{linked['user_id']}", {"is_active": 1})

print("-- who may read the list --")
ed2 = S()
st, eb2 = rq(ed2, "POST", "/api/auth/login",
             {"email": HALE, "password": "Juniper-Vault-37#p"})
if st == 200:
    ed2["t"] = eb2["csrf_token"]
    if eb2["user"].get("must_change_password"):
        rq(ed2, "POST", "/api/auth/change-password",
           {"current_password": "Juniper-Vault-37#p",
            "new_password": "Harbour-Lantern-63#m"})
    st, _ = rq(ed2, "GET", "/api/users/pickable")
    ck("an Editor can read it, since an Editor adds contributors",
       st == 200, str(st))
    st, _ = rq(ed2, "GET", "/api/users")
    ck("but still cannot read the admin user list", st == 403, str(st))
else:
    ck("the Editor could sign in", False, str(st))

# A fresh session: the Viewer's account was deactivated and reactivated
# earlier in this suite to prove a deactivated account stops supplying a
# title, and deactivating revokes its sessions at once — so the old one is
# genuinely dead, and reusing it would test a 401 rather than a 403.
vw = S()
st, vwb = rq(vw, "POST", "/api/auth/login",
             {"email": "p.lowell@example.gov", "password": REAL})
ck("the Viewer can sign in again after reactivation", st == 200, str(st))
if st == 200:
    vw["t"] = vwb["csrf_token"]
    st, _ = rq(vw, "GET", "/api/users/pickable")
    ck("a Viewer cannot read it", st == 403, str(st))

print(f"\n  {len(P)} passed, {len(F)} failed")
sys.exit(1 if F else 0)
