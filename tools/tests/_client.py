"""Signing in, shared by every feature suite.

    python3 tools/tests/t_tasks.py --email you@example.gov
    python3 tools/tests/t_tasks.py 8765 --email you@example.gov

Every suite used to open with this, copied eleven times:

    st, b = rq(boss, "POST", "/api/auth/login",
               {"email": "ben.silberglitt@example.gov",
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
behavior as `selftest.py`, including refusing to run against an account whose
password is still temporary — that account cannot do anything until it is
changed, and changing it is the owner's business.

`--password` and `TEST_PASSWORD` exist for the assistant's harness, which has
no terminal to prompt at. Prefer the prompt: a password on a command line ends
up in shell history, and `!` or `#` in one is easily mangled.
"""
import getpass
import time
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


def request(s, method, path, body=None, extra_headers=None):
    """(status, parsed-body). The real status code, so 201 is visible.

    `extra_headers` exists for one test that cannot be written without it:
    proving a password reset link is built from the `app_base_url` setting
    rather than from the request's Host header. Sending a forged Host is the
    only way to assert that, and it has to go through the same helper as every
    other call so the session and CSRF handling are identical.
    """
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if s.get("t"):
        headers["X-CSRF-Token"] = s["t"]
    headers.update(extra_headers or {})
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


def ensure_account(boss, rq, email, first, last, role, temp, extra=None):
    """Create a test account, or reset one an earlier run left behind.

    Three suites needed this and each had written it as "create, then log in".
    That works exactly once. On a second run against the same database the
    POST is a 409, the stored password is whatever the first run changed it to
    rather than `temp`, the login returns 401, and **every later assertion
    about that person fails** — reported as the permission checks being broken
    rather than as the sign-in never happening. Ben hit it on `t_tasks`: nine
    failures, all role checks, none of them the actual fault. `t_052` and
    `t_060` had the same bug waiting.

    So the account is created **or reset**: if it already exists, the
    SuperAdmin issues a fresh temporary password and makes sure it is active
    and holds the right role.

    **Resetting here is not the footgun this module exists to prevent.** That
    rule is that a suite must not change the password of the account it
    *borrows* to sign in as — a real person's. These accounts are the suite's
    own: it creates them, names them for itself, and nobody else signs in as
    them. Ownership is the whole distinction.

    Returns `(status, session, password)`. The session's `t` is None if the
    sign-in failed, so a caller that wants to assert on it can. `password` is
    what the account holds **afterwards**, which a later re-login has to use —
    it is not `temp`, because the first sign-in has to change it.
    """
    body = {"email": email, "first_name": first, "last_name": last,
            "role": role, "password": temp}
    body.update(extra or {})
    st, _ = rq(boss, "POST", "/api/users", body)
    created = st in (200, 201)
    if not created:
        st_u, users = rq(boss, "GET", "/api/users")
        # A bare list. Written defensively because this branch only runs on a
        # SECOND run, so a mistake in it passes every fresh-database test and
        # shows up only where it is hardest to notice.
        rows = users if isinstance(users, list) else (users.get("users") or [])
        uid = next((u["user_id"] for u in rows
                    if isinstance(u, dict) and u.get("email") == email), None)
        if uid:
            reset = {"password": temp, "is_active": 1, "role": role}
            reset.update(extra or {})
            rq(boss, "PUT", f"/api/users/{uid}", reset)
        else:
            # Creation failed AND there is no account to fall back on, so
            # everything after this is about a person who does not exist.
            # Printed loudly rather than returned quietly, because the caller
            # gets a plausible-looking password either way and the failures
            # then point at the feature instead of at this.
            #
            # The way this happens in practice: the temporary password
            # contains the first or last name being passed in, which the
            # password policy refuses. `Temp-Pw-For-Reset-1!` for a person
            # called Reset Me is refused with "Must not contain your name".
            print(f"\n  !! could not create or find {email}: {st} {_short(_)}"
                  f"\n  !! every later check about that account is meaningless."
                  f"\n  !! if the message mentions the name, the temporary "
                  f"password contains it.\n")

    s = session()
    s["base"] = BASE
    held = temp
    st_in, b = rq(s, "POST", "/api/auth/login",
                  {"email": email, "password": temp})
    if isinstance(b, dict):
        s["t"] = b.get("csrf_token")
        if (b.get("user") or {}).get("must_change_password"):
            # It has to become something ELSE: change-password refuses a new
            # password equal to the current one. That is exactly what made the
            # original code unrepeatable — the account ended each run holding a
            # password the next run did not know. It does not matter here,
            # because the reset above reissues `temp` every time, so what the
            # account ends up holding is never needed again.
            rq(s, "POST", "/api/auth/change-password",
               {"current_password": temp, "new_password": temp + "R2"})
            held = temp + "R2"
    return st_in, s, held


def _short(payload):
    try:
        return json.dumps(payload)[:200]
    except Exception:
        return str(payload)[:200]


def unique_email(local, domain="example.gov"):
    """An address no earlier run can have used.

    `example.gov` rather than a real domain, and that is a safety property
    rather than tidiness: `example.gov` is IANA-reserved and can never receive
    mail, so a misconfigured send cannot reach a real person. That began to
    matter in 0.9.0, when the app gained a mailer. These were `maine.gov`
    addresses until 0.9.1, which also read as real colleagues in a repository
    about to be made public.

    `ensure_account` covers an account a suite reuses. It cannot help a check
    that needs an account **nobody has touched** — "a title given at creation
    is stored", or "an unlinked account is offered in the picker", which is
    false the moment a previous run linked it. Those need a new address every
    time, and the cost is one spare account per run on a local database that
    is already full of test debris.
    """
    return f"{local}+{int(time.time() * 1000)}@{domain}"


def report(passed, failed):
    """The closing summary every suite prints, and the exit status."""
    print(f"\n{len(passed)} passed, {len(failed)} failed")
    for f in failed:
        print("  FAILED:", f)
    return 1 if failed else 0
