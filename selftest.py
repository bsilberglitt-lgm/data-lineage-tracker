#!/usr/bin/env python3
"""End-to-end check of the running app against PostgreSQL.

Run this after starting the stack to confirm the migration worked. It exercises
every API endpoint, the governance inheritance chain, and the read/write paths,
then restores anything it changed.

Usage:
    python3 selftest.py                          # tests http://localhost:8765
    python3 selftest.py http://host:port         # tests another address
    python3 selftest.py https://app.onrender.com # tests the deployed app
    python3 selftest.py <url> --insecure         # skip TLS verification

Only the Python standard library is required, so you can run this from your Mac
without installing anything.
"""

import json
import ssl
import sys
import urllib.error
import urllib.request

_args = [a for a in sys.argv[1:] if not a.startswith("--")]
INSECURE = "--insecure" in sys.argv

BASE = (_args[0] if _args else "http://localhost:8765").rstrip("/")


def _ssl_context():
    """Build a TLS context that works on stock macOS Python installs.

    Python from python.org does not use the macOS system trust store, so
    certificate verification fails until its bundled certificates are
    installed. Prefer certifi when available, and allow --insecure as an
    explicit escape hatch for testing your own known URL.
    """
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


SSL_CTX = _ssl_context()

passed = 0
failed = 0
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


def request(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60, context=SSL_CTX) as r:
        raw = r.read().decode()
        ctype = r.headers.get("Content-Type", "")
        return r.status, (json.loads(raw) if "json" in ctype else raw)


def section(title):
    print(f"\n{title}")


print(f"Testing {BASE}\n" + "=" * 60)

# ---------------------------------------------------------------------------
try:
    section("Connectivity")
    status, page = request("GET", "/")
    check("GET / returns 200", status == 200)
    check("page includes the schema drill-down code", "SchemaCanvas" in page)
    check("React/Babel pinned (blank-screen fix present)",
          "@babel/standalone@7.23.5" in page)

    # -----------------------------------------------------------------------
    section("Core catalog endpoints")
    _, systems = request("GET", "/api/systems")
    check("GET /api/systems returns rows", isinstance(systems, list) and len(systems) > 0,
          f"got {type(systems).__name__}")
    _, flows = request("GET", "/api/flows")
    check("GET /api/flows returns rows", isinstance(flows, list) and len(flows) > 0)
    _, elements = request("GET", "/api/elements")
    check("GET /api/elements returns rows", isinstance(elements, list) and len(elements) > 0)
    _, stakeholders = request("GET", "/api/stakeholders")
    check("GET /api/stakeholders responds", isinstance(stakeholders, list))
    _, agreements = request("GET", "/api/agreements")
    check("GET /api/agreements responds", isinstance(agreements, list))

    print(f"        ({len(systems)} systems, {len(flows)} flows, {len(elements)} elements)")

    # Booleans must still be 0/1 integers, not true/false — the frontend
    # governance selects compare against the strings "1"/"0".
    pii_values = {type(s.get("contains_pii")).__name__ for s in systems}
    check("contains_pii is an integer (API contract preserved)",
          pii_values <= {"int"}, f"saw types: {pii_values}")

    # -----------------------------------------------------------------------
    section("Schema drill-down")
    with_schema = [s for s in systems if s.get("schema_table_count")]
    check("at least one system reports a schema", len(with_schema) > 0)
    for s in with_schema:
        print(f"        {s['system_id']} {s['name']} -> {s['schema_table_count']} tables")

    if with_schema:
        sid = with_schema[0]["system_id"]
        _, schema = request("GET", f"/api/systems/{sid}/schema")
        tables = schema.get("tables", [])
        check(f"GET /api/systems/{sid}/schema returns tables", len(tables) > 0)
        total_cols = sum(len(t.get("columns", [])) for t in tables)
        check("tables carry their columns", total_cols > 0, f"{total_cols} columns")
        print(f"        {len(tables)} tables, {total_cols} columns")

        # ---- Governance inheritance + override roundtrip -------------------
        section("Governance inheritance and overrides")
        tid = tables[0]["table_id"]
        _, tbl = request("GET", f"/api/schema/tables/{tid}")
        check("table detail includes resolved governance", "governance" in tbl)
        check("table detail includes inherited values", "inherited" in tbl)

        original = tbl.get("data_classification")  # None means "inheriting"
        base = tbl["governance"]["data_classification"]
        check("classification resolves from somewhere",
              base["source"] in ("system", "table", "column", "none"),
              f"source={base['source']}")

        _, after = request("PUT", f"/api/schema/tables/{tid}",
                           {"data_classification": "Public"})
        check("override saves and reports source 'table'",
              after["governance"]["data_classification"] == {"value": "Public",
                                                             "source": "table"},
              f"got {after['governance']['data_classification']}")

        _, cleared = request("PUT", f"/api/schema/tables/{tid}",
                             {"data_classification": ""})
        check("clearing an override reverts to inheritance",
              cleared["governance"]["data_classification"]["source"] != "table",
              f"got {cleared['governance']['data_classification']}")

        # restore whatever was there before
        request("PUT", f"/api/schema/tables/{tid}",
                {"data_classification": original if original else ""})

        # ---- Column level ---------------------------------------------------
        cols = [c for t in tables for c in t.get("columns", [])]
        if cols:
            cid = cols[0]["column_id"]
            _, col = request("GET", f"/api/schema/columns/{cid}")
            check("column detail includes governance", "governance" in col)
            check("column detail includes parent table", "parent_table" in col)
            check("upstream lineage placeholder present", "upstream_lineage" in col)

            col_original = col.get("data_classification")
            _, col_after = request("PUT", f"/api/schema/columns/{cid}",
                                   {"data_classification": "Confidential"})
            check("column override reports source 'column'",
                  col_after["governance"]["data_classification"]["source"] == "column",
                  f"got {col_after['governance']['data_classification']}")
            request("PUT", f"/api/schema/columns/{cid}",
                    {"data_classification": col_original if col_original else ""})

        # ---- Position persistence ------------------------------------------
        section("Writes persist")
        orig_x, orig_y = tables[0].get("x_pos"), tables[0].get("y_pos")
        request("PUT", f"/api/schema/tables/{tid}/position", {"x_pos": 111, "y_pos": 222})
        _, moved = request("GET", f"/api/schema/tables/{tid}")
        check("table position persists",
              float(moved["x_pos"]) == 111 and float(moved["y_pos"]) == 222,
              f"got {moved['x_pos']},{moved['y_pos']}")
        request("PUT", f"/api/schema/tables/{tid}/position",
                {"x_pos": orig_x, "y_pos": orig_y})

    # -----------------------------------------------------------------------
    section("Create and delete (write path)")
    _, created = request("POST", "/api/systems", {
        "name": "SELFTEST — delete me", "system_type": "Source",
        "managed_by": "selftest", "description": "temporary row created by selftest.py",
    })
    new_id = created.get("system_id")
    check("POST /api/systems creates a row", bool(new_id), f"got {created}")
    if new_id:
        _, fetched = request("GET", f"/api/systems/{new_id}")
        check("created system is readable", fetched.get("name") == "SELFTEST — delete me")
        _, edited = request("PUT", f"/api/systems/{new_id}", {"notes": "edited by selftest"})
        check("system update persists", edited.get("notes") == "edited by selftest")
        request("DELETE", f"/api/systems/{new_id}")
        try:
            request("GET", f"/api/systems/{new_id}")
            check("test system cleaned up", False, "row still exists after DELETE")
        except urllib.error.HTTPError as e:
            check("test system cleaned up", e.code == 404, f"status {e.code}")

except urllib.error.URLError as e:
    reason = getattr(e, "reason", e)
    if isinstance(reason, ssl.SSLCertVerificationError):
        print(f"\nTLS certificate verification failed for {BASE}")
        print("\nThis is a local Python issue, not a problem with the deployed app.")
        print("Python installed from python.org does not use the macOS system")
        print("certificate store. Fix it once with:\n")
        version = f"{sys.version_info.major}.{sys.version_info.minor}"
        print(f'    /Applications/Python\\ {version}/Install\\ Certificates.command\n')
        print("Or install certifi:   pip3 install certifi")
        print(f"Or skip verification: python3 selftest.py {BASE} --insecure")
        sys.exit(2)
    print(f"\nCould not reach {BASE}: {e}")
    if "localhost" in BASE or "127.0.0.1" in BASE:
        print("Is the app running?  docker compose up")
    else:
        print("Check the service URL, and that the service is awake "
              "(a free instance can take ~1 minute to spin up).")
    sys.exit(2)

# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
print(f"{passed} passed, {failed} failed")
if failed:
    print("\nFailed checks:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("\nAll checks passed — the PostgreSQL migration is working.")
