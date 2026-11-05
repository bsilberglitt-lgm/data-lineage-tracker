"""Signing in, shared by every feature suite.

    python3 tools/tests/t_tasks.py --email you@example.gov
    python3 tools/tests/t_tasks.py 8765 --email you@example.gov

Every suite used to open with this, copied eleven times:

    st, b = rq(boss, "POST", "/api/auth/login",
               {"email": "ben.silberglitt@maine.gov",
                "password": "Bootstrap-Temp-7xQ!"})
    boss["t"] = b["csrf_token"]
    rq(boss, "POST", "/api/auth/change-password",
       {"current_password": "Bootstrap-Temp-7xQ!",
        "new_password": "Quartz-Vault-88#z"})

Two things wrong with that, and the second is the serious one.

**It only works on a database nobody has used.** Those are the bootstrap
credentials the assistant's sandbox creates. On a real local database the
password is whatever its owner set, the login fails, and the next line dies
with `KeyError: 'csrf_token'` — which says nothing about the cause. Ben hit
exactly that at checklist step 3b.

**And if the login HAD worked it would have changed his password** to a string
out of a test file. A test that rewrites the credentials of the account it
borrows is not a test, it is a footgun; it only stayed harmless because it
could not get in.

So: credentials come from the command line, the password is prompted for when
it is not supplied, and **nothing here ever changes a password.** Same
behaviour as `selftest.py`, including refusing to run against an account whose
password is still temporary — that account cannot do anything until it is
changed, and changing it is the owner's business.

`--password` and `TEST_PASSWORD` exist for the assistant's harness, which has
no terminal to prompt at. Prefer the prompt: a password on a command line ends
up in shell history, and `!` or `#` in one is easily mangled.
"""
import getpass
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.request


def _args(argv):
    """Port, email and password out of argv, in any order.

    The port is positional because every suite has always taken it that way
    and the checklist is written with it; everything else is a flag.
    """
    port, email, password = None, None, None
    rest = list(argv[1:])
    while rest:
        a = rest.pop(0)
        if a in ("--email", "-e") and rest:
            email = rest.pop(0)
        elif a in ("--password", "-p") and rest:
            password = rest.pop(0)
        elif a in ("--help", "-h"):
            print(__doc__)
            sys.exit(0)
        elif a.startswith("-"):
            print(f"Unknown option {a!r}. Try --help.")
            sys.exit(2)
        elif a.isdigit():
            port = a
        elif a.startswith("http"):
            port = a                      # a whole base URL also works
    return port, email, password


def base_url(argv=None):
    port, _, _ = _args(argv or sys.argv)
    if port and str(port).startswith("http"):
        return str(port).rstrip("/")
    return "http://localhost:" + (port or os.environ.get("PORT") or "8765")


def session():
    """A cookie-keeping session. `t` holds the CSRF token once signed in."""
    cj = http.cookiejar.CookieJar()
    return {"op": urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cj)), "t": None, "base": None}


def request(s, method, path, body=None):
    """(status, parsed-body). The real status code, so 201 is visible."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if s.get("t"):
        headers["X-CSRF-Token"] = s["t"]
    req = urllib.request.Request((s.get("base") or BASE) + path,
                                 data=data, method=method, headers=headers)
    try:
        resp = s["op"].open(req)
        raw = resp.read().decode()
        return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw[:200]
    except urllib.error.URLError as e:
        print(f"\nCould not reach {s.get('base') or BASE}: {e.reason}")
        print("Is the app running?  docker compose up")
        sys.exit(2)


BASE = base_url()


def sign_in(argv=None):
    """A signed-in session, or a clear explanation and a non-zero exit.

    Never returns a session that is not usable, so a suite can go straight on
    to its first request without checking anything.
    """
    argv = argv or sys.argv
    _, email, password = _args(argv)
    email = email or os.environ.get("TEST_EMAIL")
    password = password or os.environ.get("TEST_PASSWORD")

    print(f"Testing {BASE}")
    if not email:
        try:
            email = input("Email: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(2)
    if not password:
        try:
            password = getpass.getpass("Password: ")
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(2)
    if not email or not password:
        print("An email and password are required.")
        sys.exit(2)

    s = session()
    s["base"] = BASE
    st, body = request(s, "POST", "/api/auth/login",
                       {"email": email, "password": password})
    # Checked rather than assumed: reaching into the response for a token that
    # is not there is what produced `KeyError: 'csrf_token'` and told nobody
    # anything.
    if st != 200 or not isinstance(body, dict) or not body.get("csrf_token"):
        print("\nCould not sign in.")
        detail = body.get("error") if isinstance(body, dict) else body
        if detail:
            print(f"  {detail}")
        print("  Check the email and password, and that this is the right app.")
        sys.exit(2)
    s["t"] = body["csrf_token"]

    if (body.get("user") or {}).get("must_change_password"):
        print("\nThis account still has a temporary password.")
        print("Change it in the app first, then re-run with the new one.")
        print("(These suites deliberately do not change it for you.)")
        sys.exit(2)

    who = (body.get("user") or {})
    print(f"Signed in as {who.get('email', email)} ({who.get('role', '?')})\n")
    return s


def sign_in_with(s, rq, argv=None):
    """Sign in using a suite's **own** `rq`, which several of them have.

    The older suites were written in a compact style and their `rq` differs:
    one takes an extra `sc=` scenario argument, another returns three values.
    Rewriting them to use `request` above would be a bigger change than the
    bug warrants, so they keep their helper and borrow the part that matters —
    the credentials, the check, and never touching a password.

    `rq` is called as `rq(s, "POST", path, body)` and may return either
    `(status, body)` or `(status, body, extra)`.
    """
    argv = argv or sys.argv
    _, email, password = _args(argv)
    email = email or os.environ.get("TEST_EMAIL")
    password = password or os.environ.get("TEST_PASSWORD")

    print(f"Testing {BASE}")
    if not email:
        try:
            email = input("Email: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(2)
    if not password:
        try:
            password = getpass.getpass("Password: ")
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(2)
    if not email or not password:
        print("An email and password are required.")
        sys.exit(2)

    out = rq(s, "POST", "/api/auth/login",
             {"email": email, "password": password})
    st, body = out[0], out[1]
    if st != 200 or not isinstance(body, dict) or not body.get("csrf_token"):
        print("\nCould not sign in.")
        detail = body.get("error") if isinstance(body, dict) else body
        if detail:
            print(f"  {detail}")
        print("  Check the email and password, and that this is the right app.")
        sys.exit(2)
    s["t"] = body["csrf_token"]
    if (body.get("user") or {}).get("must_change_password"):
        print("\nThis account still has a temporary password.")
        print("Change it in the app first, then re-run with the new one.")
        print("(These suites deliberately do not change it for you.)")
        sys.exit(2)
    who = (body.get("user") or {})
    print(f"Signed in as {who.get('email', email)} ({who.get('role', '?')})\n")
    return s


def report(passed, failed):
    """The closing summary every suite prints, and the exit status."""
    print(f"\n{len(passed)} passed, {len(failed)} failed")
    for f in failed:
        print("  FAILED:", f)
    return 1 if failed else 0
