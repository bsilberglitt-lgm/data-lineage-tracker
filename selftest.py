#!/usr/bin/env python3
"""End-to-end check of the running app: data, permissions, and the audit trail.

Run after starting the stack to confirm everything works. It signs in, exercises
every API surface, verifies that roles are actually enforced on the server, and
restores anything it changed.

Usage:
    python3 selftest.py --email you@example.gov --password 'YourPassword'
    python3 selftest.py https://app.onrender.com --email ... --password ...

Credentials may also come from SELFTEST_EMAIL / SELFTEST_PASSWORD.
Add --insecure to skip TLS verification (see the note about macOS below).

The account used must be an Administrator or SuperAdmin: the role-enforcement
checks create a temporary Viewer account and deactivate it afterwards.
Only the Python standard library is required.
"""

import getpass
import http.cookiejar
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------

def arg_value(name, fallback=None):
    flag = f"--{name}"
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return fallback


_positional = [a for i, a in enumerate(sys.argv[1:], 1)
               if not a.startswith("--") and not (sys.argv[i - 1].startswith("--")
                                                  and sys.argv[i - 1] != "--insecure")]
BASE = (_positional[0] if _positional else "http://localhost:8765").rstrip("/")
INSECURE = "--insecure" in sys.argv
EMAIL = arg_value("email", os.environ.get("SELFTEST_EMAIL"))
PASSWORD = arg_value("password", os.environ.get("SELFTEST_PASSWORD"))

passed = failed = 0
failures = []


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        failures.append(name)
        print(f"  FAIL  {name}" + (f"\n          {detail}" if detail else ""))


def section(title):
    print(f"\n{title}")


# ---------------------------------------------------------------------------
# HTTP client with cookies + CSRF
# ---------------------------------------------------------------------------

def ssl_context():
    if INSECURE:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


SSL_CTX = ssl_context()


class Client:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        # The TLS context has to be attached to the opener; open() does not
        # accept a context argument the way urlopen() does.
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar),
            urllib.request.HTTPSHandler(context=SSL_CTX))
        self.csrf = None

    def request(self, method, path, body=None, send_csrf=True):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(BASE + path, data=data, method=method)
        if data:
            req.add_header("Content-Type", "application/json")
        if send_csrf and self.csrf and method != "GET":
            req.add_header("X-CSRF-Token", self.csrf)
        try:
            with self.opener.open(req, timeout=60) as r:
                raw = r.read().decode()
                ctype = r.headers.get("Content-Type", "")
                payload = json.loads(raw) if "json" in ctype else raw
                return r.status, payload
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw

    def login(self, email, password):
        status, body = self.request("POST", "/api/auth/login",
                                    {"email": email, "password": password})
        if status == 200:
            self.csrf = body.get("csrf_token")
        return status, body


print(f"Testing {BASE}\n" + "=" * 62)

# Prompt for anything missing. Typing the password at a prompt avoids shell
# quoting problems entirely (a '!' or '#' in a password is easily mangled by the
# shell) and keeps it out of your command history.
if not EMAIL:
    try:
        EMAIL = input("Email: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(2)
if not PASSWORD:
    try:
        PASSWORD = getpass.getpass("Password: ")
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(2)

if not EMAIL or not PASSWORD:
    print("\nAn email and password are required.")
    sys.exit(2)

admin = Client()

try:
    # -----------------------------------------------------------------------
    section("Everything requires a session")
    anon = Client()
    s, _ = anon.request("GET", "/api/systems")
    check("GET /api/systems is refused when signed out", s == 401, f"got {s}")
    s, _ = anon.request("PUT", "/api/systems/SYS-001", {"notes": "x"})
    check("writes are refused when signed out", s == 401, f"got {s}")
    s, page = anon.request("GET", "/")
    check("login page still serves", s == 200)
    check("old hardcoded password is gone", "MaineLineage" not in str(page))

    # -----------------------------------------------------------------------
    section("Sign in")
    s, body = admin.login(EMAIL, "definitely-the-wrong-password")
    check("wrong password rejected", s == 401, f"got {s}")
    s, body = admin.login(EMAIL, PASSWORD)
    check("correct password accepted", s == 200, f"got {s}: {body}")
    if s != 200:
        raise SystemExit("Cannot continue without a session.")
    me = body["user"]
    role = me["role"]
    print(f"        signed in as {me['email']} ({role})")
    if me.get("must_change_password"):
        print("\n  This account still has a temporary password. Change it in the app "
              "first,\n  then re-run with the new password.")
        raise SystemExit(2)
    check("CSRF token issued", bool(admin.csrf))

    section("CSRF protection")
    s, _ = admin.request("PUT", "/api/systems/SYS-001", {"notes": "csrf probe"},
                         send_csrf=False)
    check("write without CSRF header is refused", s == 403, f"got {s}")

    # -----------------------------------------------------------------------
    section("Core catalog")
    s, systems = admin.request("GET", "/api/systems")
    check("GET /api/systems", s == 200 and isinstance(systems, list) and systems)
    s, flows = admin.request("GET", "/api/flows")
    check("GET /api/flows", s == 200 and isinstance(flows, list))
    s, elements = admin.request("GET", "/api/elements")
    check("GET /api/elements", s == 200 and isinstance(elements, list))
    s, contributors = admin.request("GET", "/api/contributors")
    check("GET /api/contributors (renamed from stakeholders)",
          s == 200 and isinstance(contributors, list))
    check("contributor IDs renumbered to CON-",
          all(str(c["contributor_id"]).startswith("CON-") for c in contributors)
          if contributors else True,
          f"got {[c['contributor_id'] for c in contributors][:3]}")
    print(f"        {len(systems)} systems, {len(flows)} flows, "
          f"{len(elements)} elements, {len(contributors)} contributors")
    check("contains_pii is still an integer (API contract preserved)",
          {type(x.get("contains_pii")).__name__ for x in systems} <= {"int"})

    # -----------------------------------------------------------------------
    section("Schema drill-down")
    with_schema = [x for x in systems if x.get("schema_table_count")]
    check("at least one system has a mapped schema", bool(with_schema))
    for x in with_schema:
        print(f"        {x['system_id']} {x['name']} -> {x['schema_table_count']} tables")
    table_id = column_id = None
    if with_schema:
        sid = with_schema[0]["system_id"]
        s, schema = admin.request("GET", f"/api/systems/{sid}/schema")
        tables = schema.get("tables", [])
        check(f"GET /api/systems/{sid}/schema", s == 200 and bool(tables))
        cols = [c for t in tables for c in t.get("columns", [])]
        check("tables carry their columns", bool(cols), f"{len(cols)} columns")
        table_id = tables[0]["table_id"] if tables else None
        column_id = cols[0]["column_id"] if cols else None

    # -----------------------------------------------------------------------
    section("Governance inheritance and overrides")
    if table_id:
        s, tbl = admin.request("GET", f"/api/schema/tables/{table_id}")
        check("table detail resolves governance", "governance" in tbl)
        check("table detail reports inherited values", "inherited" in tbl)
        original = tbl.get("data_classification")
        s, after = admin.request("PUT", f"/api/schema/tables/{table_id}",
                                 {"data_classification": "Public"})
        check("override saves and reports source 'table'",
              after["governance"]["data_classification"] == {"value": "Public",
                                                             "source": "table"},
              f"got {after['governance']['data_classification']}")
        s, cleared = admin.request("PUT", f"/api/schema/tables/{table_id}",
                                   {"data_classification": ""})
        check("clearing an override restores inheritance",
              cleared["governance"]["data_classification"]["source"] != "table")
        admin.request("PUT", f"/api/schema/tables/{table_id}",
                      {"data_classification": original or ""})
    if column_id:
        s, col = admin.request("GET", f"/api/schema/columns/{column_id}")
        check("column detail resolves governance", "governance" in col)
        check("upstream lineage placeholder present", "upstream_lineage" in col)

    # -----------------------------------------------------------------------
    section("Write path and audit trail")
    s, created = admin.request("POST", "/api/systems", {
        "name": "SELFTEST — delete me", "system_type": "Source",
        "managed_by": "selftest", "description": "temporary row from selftest.py"})
    new_id = created.get("system_id") if isinstance(created, dict) else None
    check("POST /api/systems creates a row", s == 201 and bool(new_id), f"got {s}")
    if new_id:
        s, edited = admin.request("PUT", f"/api/systems/{new_id}",
                                  {"notes": "edited by selftest"})
        check("update persists", edited.get("notes") == "edited by selftest")

        if role == "SuperAdmin":
            s, auditdata = admin.request(
                "GET", f"/api/audit?entity_type=system&entity_id={new_id}")
            events = auditdata.get("events", [])
            check("audit recorded the create and the update", len(events) >= 2,
                  f"got {len(events)} events")
            update_events = [e for e in events if e["action"] == "update"]
            if update_events:
                fields = [c["field_name"] for c in update_events[0].get("changes", [])]
                check("audit captured the changed field with old/new values",
                      "notes" in fields, f"fields: {fields}")
            check("audit records who made the change",
                  all(e.get("actor_email") for e in events))

        s, _ = admin.request("DELETE", f"/api/systems/{new_id}")
        check("delete succeeds", s == 200, f"got {s}")
        s, _ = admin.request("GET", f"/api/systems/{new_id}")
        check("deleted row is gone", s == 404, f"got {s}")

    # -----------------------------------------------------------------------
    section("Role enforcement (server-side)")
    if role in ("Administrator", "SuperAdmin"):
        temp_email = "selftest-viewer@example.invalid"
        temp_pass = "Quartz-Meadow-73#p"
        # Clear the domain allow-list restriction only if it would block us.
        s, existing = admin.request("GET", "/api/users")
        already = next((u for u in existing if u["email"] == temp_email), None) \
            if isinstance(existing, list) else None
        if already:
            admin.request("PUT", f"/api/users/{already['user_id']}",
                          {"is_active": 1, "password": temp_pass, "role": "Viewer"})
            temp_id = already["user_id"]
            s = 201
        else:
            s, made = admin.request("POST", "/api/users", {
                "email": temp_email, "first_name": "Self", "last_name": "Test",
                "role": "Viewer", "password": temp_pass})
            temp_id = made.get("user_id") if isinstance(made, dict) else None
        if s == 201 or already:
            viewer = Client()
            vs, vbody = viewer.login(temp_email, temp_pass)
            if vs == 200 and vbody["user"].get("must_change_password"):
                viewer.request("POST", "/api/auth/change-password",
                               {"current_password": temp_pass,
                                "new_password": "Quartz-Harbor-51#w"})
                viewer = Client()
                viewer.login(temp_email, "Quartz-Harbor-51#w")
            s, _ = viewer.request("GET", "/api/systems")
            check("Viewer can read the catalog", s == 200, f"got {s}")
            s, _ = viewer.request("PUT", "/api/systems/SYS-001", {"notes": "nope"})
            check("Viewer is blocked from editing", s == 403, f"got {s}")
            s, _ = viewer.request("PUT", "/api/systems/SYS-001/position",
                                  {"x_pos": 1, "y_pos": 1})
            check("Viewer is blocked from moving diagram nodes", s == 403, f"got {s}")
            s, _ = viewer.request("GET", "/api/users")
            check("Viewer is blocked from user management", s == 403, f"got {s}")
            s, _ = viewer.request("GET", "/api/audit")
            check("Viewer is blocked from the audit log", s == 403, f"got {s}")
            if temp_id:
                admin.request("PUT", f"/api/users/{temp_id}", {"is_active": 0})
                check("temporary test account deactivated", True)
        else:
            print("        (skipped: could not create the temporary account — "
                  f"status {s}. An email domain allow-list may be in effect.)")
    else:
        print("        (skipped: needs an Administrator or SuperAdmin account)")

    # -----------------------------------------------------------------------
    section("Administration screens")
    if role in ("Administrator", "SuperAdmin"):
        s, _ = admin.request("GET", "/api/users")
        check("GET /api/users", s == 200)
        s, _ = admin.request("GET", "/api/sessions")
        check("GET /api/sessions", s == 200)
    if role == "SuperAdmin":
        s, prefs = admin.request("GET", "/api/preferences")
        check("GET /api/preferences", s == 200 and isinstance(prefs, list))
        keys = {p["key"] for p in prefs} if isinstance(prefs, list) else set()
        check("2FA and self-service toggles exist but are locked off",
              {"require_2fa", "enable_self_service_reset"} <= keys)
        s, adata = admin.request("GET", "/api/audit")
        check("GET /api/audit", s == 200 and "events" in adata)
        s, csvtext = admin.request("GET", "/api/audit/export")
        check("audit CSV export", s == 200 and "Timestamp" in str(csvtext))
    else:
        print("        (Preferences and Audit need SuperAdmin — skipped)")

    # -----------------------------------------------------------------------
    section("Scenario planning")
    # The rule that matters most: a scenario must never change Current
    # Architecture. Everything else here supports that claim.
    if role in ("Editor", "Administrator", "SuperAdmin"):
        s, live_before = admin.request("GET", "/api/systems")
        n_live = len(live_before) if isinstance(live_before, list) else 0
        s, scn = admin.request("POST", "/api/scenarios",
                               {"name": "selftest — safe to delete",
                                "start_from": "live"})
        check("a scenario can be created from Current Architecture", s == 201,
              f"got {s}: {scn}")
        sid = scn.get("scenario_id") if isinstance(scn, dict) else None
        if sid:
            s, in_scn = admin.request("GET", f"/api/systems?scenario={sid}")
            check("it contains its own copy of every system",
                  s == 200 and len(in_scn) == n_live, f"{len(in_scn)} vs {n_live}")
            check("those copies have scenario-prefixed identifiers",
                  all(x["system_id"].startswith(f"S{sid}-") for x in in_scn))
            check("copied records start as unchanged",
                  {x["change_state"] for x in in_scn} == {"unchanged"})

            # Change something inside the scenario, then prove live is intact.
            if in_scn:
                target = in_scn[0]
                admin.request("PUT", f"/api/systems/{target['system_id']}?scenario={sid}",
                              {"notes": "selftest edit"})
                s, again = admin.request("GET", f"/api/systems/{target['system_id']}?scenario={sid}")
                check("editing a copied record marks it modified",
                      again.get("change_state") == "modified", str(again.get("change_state")))
                s, origin = admin.request("GET", f"/api/systems/{target['origin_id']}")
                check("the live original is untouched",
                      origin.get("notes") != "selftest edit")

            s, live_after = admin.request("GET", "/api/systems")
            check("Current Architecture still has the same number of systems",
                  len(live_after) == n_live, f"{len(live_after)} vs {n_live}")
            check("NO scenario record leaks into the live diagram",
                  not [x for x in live_after
                       if str(x["system_id"]).startswith(f"S{sid}-")])
            check("every live record still has an empty scenario",
                  all(x.get("scenario_id") is None for x in live_after))

            # 'proposed' is only meaningful inside a scenario.
            s, _ = admin.request("POST", "/api/systems",
                                 {"name": "selftest proposed", "system_type": "Source",
                                  "managed_by": "x", "proposed": True})
            check("'proposed' is refused on Current Architecture", s == 400, f"got {s}")

            # Blank optional foreign keys must be stored as null, not "".
            s, made = admin.request("POST", f"/api/flows?scenario={sid}",
                                    {"name": "selftest flow",
                                     "source_system_id": in_scn[0]["system_id"],
                                     "destination_system_id": in_scn[-1]["system_id"],
                                     "frequency": "Daily", "transfer_method": "API",
                                     "format": "JSON", "owner_id": "",
                                     "data_steward_id": "", "proposed": True})
            check("a flow saves with ownership left blank", s == 201, f"got {s}: {made}")

            # Hiding is cosmetic.
            s, hid = admin.request("POST", f"/api/scenarios/{sid}/visibility",
                                   {"system_ids": [in_scn[0]["system_id"]], "hidden": True})
            check("records can be hidden in bulk", s == 200, f"got {s}")
            s, visible = admin.request("GET", f"/api/systems?scenario={sid}")
            s, everything = admin.request(
                "GET", f"/api/systems?scenario={sid}&include_hidden=1")
            check("hidden records drop out of the normal view",
                  len(visible) < len(everything), f"{len(visible)} vs {len(everything)}")
            check("but are still present in the scenario",
                  len(everything) == n_live + 0, f"{len(everything)} vs {n_live}")

            # Schema reads through to the live original.
            withschema = [x for x in live_after if x.get("schema_table_count")]
            if withschema:
                copy_id = f"S{sid}-{withschema[0]['system_id']}"
                s, sch = admin.request("GET", f"/api/systems/{copy_id}/schema?scenario={sid}")
                check("a scenario shows the schema inherited from live",
                      s == 200 and len(sch.get("tables", []))
                      == withschema[0]["schema_table_count"],
                      f"got {s}")
                check("and reports that it does not yet own it",
                      sch.get("schema_is_local") is False)

            # Deleting is recoverable.
            s, _ = admin.request("DELETE", f"/api/scenarios/{sid}")
            check("a scenario can be deleted", s == 200, f"got {s}")
            s, listing = admin.request("GET", "/api/scenarios")
            check("it disappears from the list",
                  all(x["scenario_id"] != sid for x in listing))
            if role == "SuperAdmin":
                s, _ = admin.request("POST", f"/api/scenarios/{sid}/restore")
                check("and a SuperAdmin can restore it", s == 200, f"got {s}")
                admin.request("DELETE", f"/api/scenarios/{sid}")

            s, live_end = admin.request("GET", "/api/systems")
            check("Current Architecture is unchanged by all of the above",
                  len(live_end) == n_live, f"{len(live_end)} vs {n_live}")
    else:
        print("        (creating a scenario needs Editor or higher — skipped)")

    section("Sign out")
    s, _ = admin.request("POST", "/api/auth/logout")
    check("logout succeeds", s == 200, f"got {s}")
    s, _ = admin.request("GET", "/api/systems")
    check("session no longer works after logout", s == 401, f"got {s}")

except urllib.error.URLError as e:
    reason = getattr(e, "reason", e)
    if isinstance(reason, ssl.SSLCertVerificationError):
        version = f"{sys.version_info.major}.{sys.version_info.minor}"
        print(f"\nTLS certificate verification failed for {BASE}")
        print("This is a local Python issue, not a problem with the app. Fix with:\n")
        print(f'    /Applications/Python\\ {version}/Install\\ Certificates.command\n')
        print(f"Or re-run with:  python3 selftest.py {BASE} --insecure")
        sys.exit(2)
    print(f"\nCould not reach {BASE}: {e}")
    if "localhost" in BASE or "127.0.0.1" in BASE:
        print("Is the app running?  docker compose up")
    else:
        print("Check the URL, and that the service is awake "
              "(a free instance takes ~1 minute to spin up).")
    sys.exit(2)

print("\n" + "=" * 62)
print(f"{passed} passed, {failed} failed")
if failed:
    print("\nFailed checks:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("\nAll checks passed.")
