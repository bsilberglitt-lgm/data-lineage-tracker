#!/usr/bin/env python3
"""Demonstrate the scenario rules against a running copy of the app.

This is a review aid, not a test suite. It performs the operations described in
SCENARIOS.md and narrates what happened in plain language, so the rules can be
judged by watching them rather than by trusting a description.

    docker compose up
    python3 tools/scenario_walkthrough.py --email you@example.gov

It prompts for the password. The account must be a SuperAdmin.

What it touches, so there are no surprises:

* Creates one scenario, works inside it, then deletes it. Deletion is soft, so
  it stays recoverable; pass --keep to leave it in place and look at it later.
* Creates one temporary Viewer account to demonstrate that a Viewer can edit
  inside a scenario but not on Current Architecture, then deactivates it.
* Creates and deletes exactly one system on Current Architecture, to prove that
  a scenario is not required for ordinary work to continue. Nothing else on the
  live diagram is written to, and the script counts live records before and
  after so you can see that for yourself.
"""

import getpass
import http.cookiejar
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

BASE = "http://localhost:8765"
for a in sys.argv[1:]:
    if a.startswith("http"):
        BASE = a.rstrip("/")


def arg(name, fallback=None):
    flag = f"--{name}"
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return fallback


KEEP = "--keep" in sys.argv
CTX = ssl.create_default_context()
if "--insecure" in sys.argv:
    CTX.check_hostname = False
    CTX.verify_mode = ssl.CERT_NONE

# Passwords for the temporary account. Deliberately unrelated to any name, since
# the password policy rejects passwords containing the account holder's name.
TEMP_FIRST_PW = "Harbour-Kestrel-31#v"
TEMP_SECOND_PW = "Meridian-Cobalt-58#j"


class Session:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar),
            urllib.request.HTTPSHandler(context=CTX))
        self.csrf = None

    def __call__(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(BASE + path, data=data, method=method)
        if data:
            req.add_header("Content-Type", "application/json")
        if self.csrf and method != "GET":
            req.add_header("X-CSRF-Token", self.csrf)
        try:
            with self.opener.open(req, timeout=60) as r:
                return r.status, json.loads(r.read().decode() or "null")
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, raw[:200]
        except urllib.error.URLError as e:
            print(f"\n  Could not reach {BASE} — is the app running?\n  {e.reason}")
            sys.exit(1)

    def login(self, email, password):
        st, b = self("POST", "/api/auth/login",
                     {"email": email, "password": password})
        if st != 200:
            return st, b
        self.csrf = b["csrf_token"]
        return st, b


# ---------------------------------------------------------------------------
# Narration
# ---------------------------------------------------------------------------

PASS, FAIL = [], []


def show(claim, ok, detail=""):
    (PASS if ok else FAIL).append(claim)
    mark = "OK  " if ok else "**  "
    line = f"  {mark}{claim}"
    if detail:
        line += f"\n        {detail}"
    print(line)


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")



def expect_list(session, path, what):
    """GET something that must be a list, or explain clearly why it wasn't.

    Without this, an error response (a dict) gets iterated as if it were a list
    of records, and the failure surfaces hundreds of lines later as an
    unreadable TypeError about string indices.
    """
    st, body = session("GET", path)
    if st == 200 and isinstance(body, list):
        return body
    print(f"\n  Could not read {what} ({path}).")
    print(f"  The server returned HTTP {st}:")
    detail = body.get("error") if isinstance(body, dict) else body
    print(f"      {detail}")
    if st == 500:
        print("\n  A 500 here usually means the database is missing a column the")
        print("  code expects. Check the app log for the column name:")
        print("      docker compose logs --tail=40 app")
        print("  then restart so pending migrations run:")
        print("      docker compose restart app")
    sys.exit(1)


def main():
    email = arg("email", os.environ.get("SELFTEST_EMAIL"))
    if not email:
        print("Usage: python3 tools/scenario_walkthrough.py --email you@example.gov")
        sys.exit(2)
    password = arg("password", os.environ.get("SELFTEST_PASSWORD")) or \
        getpass.getpass(f"Password for {email}: ")

    boss = Session()
    st, b = boss.login(email, password)
    if st != 200:
        print(f"\n  Could not sign in: {st} {b}")
        sys.exit(1)
    if b["user"]["role"] != "SuperAdmin":
        print(f"\n  This walkthrough needs a SuperAdmin; {email} is a "
              f"{b['user']['role']}.")
        sys.exit(1)
    print(f"\nSigned in to {BASE} as {email}.")

    # -- what the live diagram looks like before we start -------------------
    live_systems_before = expect_list(boss, "/api/systems", "the live systems")
    live_flows_before = expect_list(boss, "/api/flows", "the live data flows")
    n_sys, n_flow = len(live_systems_before), len(live_flows_before)
    heading("Current Architecture, before anything happens")
    print(f"  {n_sys} systems, {n_flow} data flows.")

    # -- create a scenario from the live diagram ----------------------------
    heading("1. Creating a scenario from Current Architecture")
    st, sc = boss("POST", "/api/scenarios",
                  {"name": "Walkthrough — safe to delete", "start_from": "live",
                   "description": "Created by tools/scenario_walkthrough.py"})
    if st != 201:
        print(f"  Could not create a scenario: {st} {sc}")
        sys.exit(1)
    sid = sc["scenario_id"]
    copied = sc.get("copied", {})
    show("A scenario was created and the live diagram copied into it",
         copied.get("system") == n_sys,
         f"copied {copied.get('system')} systems and "
         f"{copied.get('data_flow')} data flows")
    show("It starts as a Draft, owned by you",
         sc["status"] == "Draft" and sc["can_manage"] is True)

    in_scenario = expect_list(boss, f"/api/systems?scenario={sid}",
                              "the scenario's systems")
    show("Its systems are separate records, not the live ones",
         all(s["system_id"].startswith(f"S{sid}-") for s in in_scenario),
         f"for example {in_scenario[0]['system_id']} is this scenario's copy of "
         f"{in_scenario[0]['origin_id']}")
    show("Copied records start as 'unchanged' — nothing is proposed yet",
         {s["change_state"] for s in in_scenario} == {"unchanged"})

    # -- edit, add, retire --------------------------------------------------
    heading("2. Working inside the scenario")
    target = in_scenario[0]
    st, edited = boss("PUT", f"/api/systems/{target['system_id']}?scenario={sid}",
                      {"notes": "Reviewed during the walkthrough."})
    show("Editing a copied system marks it as 'modified' automatically",
         edited.get("change_state") == "modified")

    st, invented = boss("POST", f"/api/systems?scenario={sid}",
                        {"name": "Proposed replacement SIS",
                         "system_type": "Source", "managed_by": "TBD"})
    show("A new system inside a scenario defaults to 'proposed'",
         invented.get("change_state") == "proposed")

    flows_in = expect_list(boss, f"/api/flows?scenario={sid}",
                           "the scenario's data flows")
    if flows_in:
        st, retired = boss(
            "DELETE", f"/api/flows/{flows_in[0]['flow_id']}?scenario={sid}&mode=remove")
        show("Retiring an existing flow marks it 'removed' rather than erasing it",
             retired.get("change_state") == "removed",
             "it stays on the diagram struck through, so the plan reads as a "
             "change set")

    st, refused = boss(
        "DELETE", f"/api/systems/{invented['system_id']}?scenario={sid}&mode=remove")
    show("Trying to 'retire' something invented in the scenario is refused",
         st == 400,
         str(refused.get("error", ""))[:120])

    # -- the part that matters ----------------------------------------------
    heading("3. Current Architecture is untouched")
    live_systems_after = expect_list(boss, "/api/systems", "the live systems")
    live_flows_after = expect_list(boss, "/api/flows", "the live data flows")
    show(f"Still exactly {n_sys} systems and {n_flow} data flows",
         len(live_systems_after) == n_sys and len(live_flows_after) == n_flow,
         f"now {len(live_systems_after)} and {len(live_flows_after)}")
    strays = [s["system_id"] for s in live_systems_after
              if s["system_id"].startswith("S") and "-SYS" in s["system_id"]]
    show("No scenario records appear on the live diagram", not strays,
         f"found: {strays[:3]}" if strays else "")
    origin = next((s for s in live_systems_after
                   if s["system_id"] == target.get("origin_id")), None)
    show("The live original of the system edited above is unchanged",
         origin is not None and
         origin.get("notes") != "Reviewed during the walkthrough.")

    st, live_proposed = boss("POST", "/api/systems",
                             {"name": "Should be refused", "system_type": "Source",
                              "managed_by": "x", "proposed": True})
    show("'Proposed' is refused on Current Architecture rather than ignored",
         st == 400, str(live_proposed.get("error", ""))[:120])

    # -- ordinary live work still functions ---------------------------------
    st, made = boss("POST", "/api/systems",
                    {"name": "Walkthrough temporary system",
                     "system_type": "Source", "managed_by": "Walkthrough"})
    show("Ordinary work on Current Architecture still behaves normally",
         st == 201)
    if st == 201:
        boss("DELETE", f"/api/systems/{made['system_id']}")

    # -- a Viewer who is a scenario editor ----------------------------------
    heading("4. A Viewer invited as a scenario editor")
    temp_email = "walkthrough.temp@example.invalid"
    st, u = boss("POST", "/api/users",
                 {"email": temp_email, "first_name": "Walkthrough",
                  "last_name": "Temporary", "role": "Viewer",
                  "password": TEMP_FIRST_PW})
    if st in (200, 201):
        uid = u["user_id"]
        boss("POST", f"/api/scenarios/{sid}/members",
             {"user_id": uid, "role": "editor"})
        guest = Session()
        guest.login(temp_email, TEMP_FIRST_PW)
        guest("POST", "/api/auth/change-password",
              {"current_password": TEMP_FIRST_PW, "new_password": TEMP_SECOND_PW})
        guest = Session()
        guest.login(temp_email, TEMP_SECOND_PW)

        st_live, _ = guest("POST", "/api/systems",
                           {"name": "Nope", "system_type": "Source",
                            "managed_by": "x"})
        show("As a Viewer, they cannot add anything to Current Architecture",
             st_live == 403, f"HTTP {st_live}")
        st_scn, created = guest("POST", f"/api/systems?scenario={sid}",
                                {"name": "Added by an invited viewer",
                                 "system_type": "Source", "managed_by": "x"})
        show("But inside the scenario they invited to, they can",
             st_scn == 201, f"HTTP {st_scn}")
        st_mem, _ = guest("POST", f"/api/scenarios/{sid}/members",
                          {"user_id": uid, "role": "editor"})
        show("They still cannot change who else is in the scenario",
             st_mem == 403, f"HTTP {st_mem}")

        boss("PUT", f"/api/users/{uid}", {"is_active": 0})
        print("        (the temporary account has been deactivated)")
    else:
        print(f"  Skipped: could not create a temporary account ({st}).")

    # -- freezing -----------------------------------------------------------
    heading("5. Approving a scenario freezes it")
    boss("PUT", f"/api/scenarios/{sid}", {"status": "Approved"})
    st, frozen = boss("PUT", f"/api/systems/{target['system_id']}?scenario={sid}",
                      {"notes": "should not be possible"})
    show("Once Approved, even you as SuperAdmin cannot edit it",
         st == 409, str(frozen.get("error", ""))[:120])
    boss("PUT", f"/api/scenarios/{sid}", {"status": "Draft"})
    st, _ = boss("PUT", f"/api/systems/{target['system_id']}?scenario={sid}",
                 {"notes": "editable again"})
    show("Returning it to Draft makes it editable again", st == 200)

    # -- privacy ------------------------------------------------------------
    heading("6. Scenarios are private")
    print("  A person who is not a member is told the scenario does not exist,")
    print("  rather than that they lack permission — a name like 'Replace Vendor X'")
    print("  is itself sensitive. Administrators are the exception: they can see")
    print("  every scenario, but cannot edit one they were not invited to.")

    # -- clean up -----------------------------------------------------------
    heading("7. Cleaning up")
    if KEEP:
        print(f"  Leaving scenario {sid} in place, as requested (--keep).")
        print(f"  Delete it later from the app, or with:")
        print(f"      curl -X DELETE {BASE}/api/scenarios/{sid}")
    else:
        st, _ = boss("DELETE", f"/api/scenarios/{sid}")
        show("The walkthrough scenario was deleted", st == 200)
        st, restored = boss("POST", f"/api/scenarios/{sid}/restore")
        show("...and could be restored, because deletion is recoverable",
             st == 200)
        boss("DELETE", f"/api/scenarios/{sid}")
        print(f"        (deleted again; it is recoverable if you want to look: "
              f"scenario {sid})")

    final_systems = expect_list(boss, "/api/systems", "the live systems")
    final_flows = expect_list(boss, "/api/flows", "the live data flows")
    heading("Current Architecture, afterwards")
    same = len(final_systems) == n_sys and len(final_flows) == n_flow
    print(f"  {len(final_systems)} systems, {len(final_flows)} data flows "
          f"— {'unchanged' if same else 'CHANGED, which it should not be'}.")

    print()
    if FAIL:
        print(f"{len(PASS)} behaved as described; {len(FAIL)} did not:")
        for f in FAIL:
            print(f"  - {f}")
        sys.exit(1)
    print(f"All {len(PASS)} behaviours matched the description in SCENARIOS.md.")


if __name__ == "__main__":
    main()
