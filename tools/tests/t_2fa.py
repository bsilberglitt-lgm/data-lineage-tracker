"""Two-factor authentication (0.10.0).

    python3 tools/tests/t_2fa.py 8765 --email you@example.gov

    # with the emailed-code section, which needs the server's mail sink:
    EMAIL_SINK_PATH=/tmp/h2/mail.jsonl EMAIL_SINK_CONFIRM=discard-all-mail \
    python3 tools/tests/t_2fa.py 8900 --email you@example.gov

    # with the forced-enrollment section, which changes a deployment-wide
    # preference for the length of about ten requests (see below):
    TWOFA_GATE=yes python3 tools/tests/t_2fa.py 8900 --email you@example.gov

**Nothing here touches the second factor of the account it signs in with.**
That is the rule `_client.py` exists for, and it bites harder than usual here:
enrolling, disabling or resetting the borrowed account would change how its
owner signs in to their own app, and a half-finished enrollment would leave
them challenged for a code nothing can produce. So every enrollment happens on
an account this suite creates for itself, and the borrowed session is used only
to create that account, read preferences, and act as the administrator in the
two checks that need one.

**The account gets a fresh address every run** (`_client.unique_email`) rather
than going through `ensure_account`. That helper makes an account repeatable by
reissuing a temporary password — which does not unenroll anybody, and an
account that enrolled on the previous run answers the next run's sign-in with a
challenge instead of a session. Working around that would mean a branch that
only runs on a second run, which is exactly where `_client` records that a
mistake hides. One spare account per run on a local database already full of
test debris is the cheaper side of that trade.

**It imports `auth.py`, which no other suite does.** The rule in `CLAUDE.md` is
that no suite may import `app` — that drags in `db` and `psycopg_pool`, which
live in the Docker image and not on a Mac. `auth.py` is standard library only
(`hmac`, `hashlib`, `base64`, `struct`, `secrets`), so importing it costs
nothing and is much better than the alternative: a second implementation of
TOTP inside the test, which would agree with a wrong one just as happily as
with a right one.

**Where the secret comes from.** The suite never learns `TWOFA_SECRET_KEY` and
does not need to. Enrollment hands the derived secret to the browser once, as
base32 in `secret_groups`, because somebody has to be able to type it into an
authenticator — so the suite reads it from that response and computes codes the
same way a phone would.

What it protects:

  * **A wrong code does not complete enrollment**, and enrollment is not
    complete until a code has been proved. A salt alone means "started and
    never finished"; treating that as enrolled would challenge somebody for a
    code they cannot produce.
  * **A correct password returns a challenge, not a session**, and sets no
    session cookie. A challenge grants nothing.
  * **A challenge cannot be spent twice**, and five wrong answers kill it —
    counted against the challenge rather than the code, because a six-digit
    emailed code sits still until it expires instead of moving every thirty
    seconds.
  * **A recovery code signs in once** and never again.
  * **A recovery code earns no remembered device.** Somebody using one has
    lost their authenticator, which is the moment to trust the browser less
    rather than more.
  * **Remembering a device skips the second factor, and revoking it stops
    skipping.**
  * **Turning your own second factor off needs the password again**, because a
    session is what an attacker has if they borrowed an unlocked laptop.
  * **An administrator can clear somebody's second factor, but not somebody
    who outranks them.**
  * **No secret, code or recovery code appears in a response that should not
    carry it.**

Two things are deliberately **not** checked here, and saying so is cheaper than
leaving somebody to wonder:

  * **The lockout guard on `require_2fa`.** The preferences PUT refuses to turn
    the requirement on when no factor could work, which is only reachable on a
    deployment with no `TWOFA_SECRET_KEY` — the one configuration this suite
    refuses to run in at all, because nothing else in it would work either.
  * **That the QR code scans.** It is an image; the only instrument for that is
    a phone. `CLAUDE.md` records the two ways it has already rendered
    perfectly and failed to scan.
"""
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _client import (session, request as rq, sign_in_with,  # noqa: E402
                     unique_email, BASE)

PASSED, FAILED = [], []


def ck(label, cond, detail=""):
    (PASSED if cond else FAILED).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}"
          + (f"   [{detail}]" if not cond and detail else ""))


def load_auth():
    """`auth.py`, found without depending on the working directory.

    Beside the test file first, then the project root, then the harness's flat
    copy — the same search `t_fieldhide` and `t_fielddefs` do for
    `field_catalog`, and for the same reason: a suite that only runs from one
    directory is a suite that only runs where its author was standing.
    """
    here = Path(__file__).resolve()
    for p in (here.parent.parent.parent / "auth.py",   # project root
              Path.cwd() / "auth.py",
              here.parent / "auth.py"):                # harness flat copy
        if p.exists():
            spec = importlib.util.spec_from_file_location("dlt_auth", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    print("\nCould not find auth.py. Run this from the project, or beside a copy.")
    sys.exit(2)


auth = load_auth()

# Mirrors the constants in auth.py — read from it rather than written out, so
# changing one of these cannot leave an assertion quietly asserting the old
# value. (t_email writes RESET_RATE_LIMIT out because it may not import the
# module at all; here the module is already loaded.)
RECOVERY_TOTAL = auth.RECOVERY_CODE_COUNT
MAX_ATTEMPTS = auth.CHALLENGE_MAX_ATTEMPTS


def cookie(s, name):
    """The value of one cookie in a session's jar, or None.

    `_client.session` builds the opener around a `CookieJar` it does not keep a
    reference to, so the jar is reached through the processor the opener holds.
    Written as a loop over handlers rather than an index, because that order is
    not ours to depend on.
    """
    for h in s["op"].handlers:
        jar = getattr(h, "cookiejar", None)
        if jar is not None:
            for c in jar:
                if c.name == name:
                    return c.value
    return None


def secret_from(enroll_body):
    """The TOTP secret a phone would hold, out of the enrollment response.

    `secret_groups` is the key in groups of four for somebody to type. Padding
    is computed rather than assumed: 32 base32 characters need none, and the
    `+ "===="` that looked right the first time is wrong for exactly that
    length.
    """
    b32 = (enroll_body.get("secret_groups") or "").replace(" ", "")
    b32 += "=" * (-len(b32) % 8)
    return auth.base64.b32decode(b32)


def enroll(s):
    """Take a signed-in session from not-enrolled to enrolled.

    Returns `(secret, recovery_codes)`. Used twice — once for the ordinary
    path and once after an administrator clears the account — so the steps are
    written once.
    """
    st, body = rq(s, "POST", "/api/auth/2fa/enroll", {})
    if st != 200:
        return None, []
    secret = secret_from(body)
    st, conf = rq(s, "POST", "/api/auth/2fa/confirm",
                  {"code": auth.totp_code(secret)})
    return secret, (conf.get("recovery_codes") or [])


def fresh_login(email, password):
    """A brand-new session that has signed in with the password only.

    Returns `(session, body)`. A new session every time matters: the trusted
    device cookie lives in the jar, so reusing one would quietly skip the
    second factor and prove nothing.
    """
    s = session()
    s["base"] = BASE
    st, body = rq(s, "POST", "/api/auth/login",
                  {"email": email, "password": password})
    if st == 200 and isinstance(body, dict) and body.get("csrf_token"):
        s["t"] = body["csrf_token"]
    return s, body


def main():
    # An environment variable rather than a flag, and not by preference:
    # `_client._args` rejects any option it does not recognize, which is a
    # deliberate guard against a mistyped `--email`. It runs at import time,
    # from `base_url()`, so a suite cannot register an extra flag with it
    # before it reads argv.
    gate = (os.environ.get("TWOFA_GATE") or "").strip().lower() in (
        "1", "yes", "true")
    sink = (os.environ.get("EMAIL_SINK_PATH") or "").strip()
    if sink and (os.environ.get("EMAIL_SINK_CONFIRM") or "").strip() \
            != "discard-all-mail":
        print("\nEMAIL_SINK_PATH is set but EMAIL_SINK_CONFIRM is not.\n"
              "  The server refuses to start on the path alone, so this is\n"
              "  almost certainly not the environment you meant. Export:\n"
              "    export EMAIL_SINK_CONFIRM=discard-all-mail\n")
        return 2

    boss = sign_in_with(session(), rq)

    # ------------------------------------------------------- preconditions
    #
    # Checked as preconditions rather than as assertions, and they STOP the
    # suite. CLAUDE.md records this as the most expensive failure shape in the
    # project: a missing environment variable asserted with ck() produces a
    # screenful of failures that point everywhere except at the cause.
    st, me = rq(boss, "GET", "/api/auth/me")
    role = ((me or {}).get("user") or {}).get("role")
    boss_id = ((me or {}).get("user") or {}).get("user_id")
    if role not in ("SuperAdmin", "Administrator"):
        print(f"\nSigned in as {role}, which cannot create accounts.\n"
              "  This suite needs an Administrator or SuperAdmin, because\n"
              "  every enrollment happens on an account it creates itself.\n")
        return 2

    st, status = rq(boss, "GET", "/api/auth/2fa")
    if st != 200:
        print(f"\nGET /api/auth/2fa answered {st}. Is this build 0.10.0 or later?")
        return 2
    if status.get("totp_unavailable"):
        print(f"\n{status['totp_unavailable']}\n"
              "  Nothing in this suite can run without it. Generate one and\n"
              "  put it in .env, then restart:\n"
              '    python3 -c "import secrets; print(secrets.token_urlsafe(48))"\n'
              "    docker compose up -d\n")
        return 2

    st, prefs = rq(boss, "GET", "/api/preferences")
    before = {p["key"]: p["value"] for p in prefs}
    restore = {k: (before.get(k) or "")
               for k in ("require_2fa", "allow_email_2fa")}
    ck("the two-factor preferences both exist",
       "require_2fa" in before and "allow_email_2fa" in before,
       ", ".join(sorted(set(restore) - set(before))))

    # Everything from here is wrapped so the restore ALWAYS runs — on the way
    # out as well as on the way through. t_email learned this the hard way: a
    # restore written as the last statement of main() is skipped by the first
    # exception, and the docstring then claims something untrue.
    try:
        return body(boss, boss_id, role,
                    bool(status.get('enrolled')), sink, gate)
    finally:
        # The restore is here rather than at the end of the body so a crash
        # cannot skip it. It then has to survive whatever state that crash
        # left: the first version read `prefs2` as a list of rows and died
        # with `TypeError: string indices must be integers` when the GET
        # answered with an error object instead — so the suite crashed inside
        # the thing written to stop it crashing, and printed no summary at
        # all. Shape-checked, and a restore that cannot be *verified* says so
        # rather than reporting success.
        st_put, put_body = rq(boss, "PUT", "/api/preferences",
                              {"values": restore})
        st_get, prefs2 = rq(boss, "GET", "/api/preferences")
        print()
        if st_put != 200 or not isinstance(prefs2, list):
            ck("the two-factor preferences were put back", False,
               f"PUT {st_put} {str(put_body)[:80]} / GET {st_get} "
               f"{str(prefs2)[:80]}")
            print("  !! check Preferences by hand: this suite changed\n"
                  f"  !! {', '.join(restore)} and could not confirm the restore.")
        else:
            after = {p["key"]: p["value"] for p in prefs2}
            same = all(str(after.get(k) or "") == str(v)
                       for k, v in restore.items())
            ck("both preferences are back as they were", same,
               f"{ {k: after.get(k) for k in restore} } wanted { restore }")
        print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
        for f in FAILED:
            print("  FAILED:", f)


def body(boss, boss_id, boss_role, boss_enrolled, sink, gate):
    addr = unique_email("twofa")
    TEMP = "Quartz-Harbor-91#z"
    PW = "Tundra-Lantern-42#q"

    print("\n-- an account of this suite's own --")
    st, created = rq(boss, "POST", "/api/users",
                     {"email": addr, "first_name": "Wren", "last_name": "Pike",
                      "role": "Editor", "password": TEMP, "is_active": 1})
    ck("account created", st in (200, 201), f"{st} {created}")
    uid = created.get("user_id") or (created.get("user") or {}).get("user_id")
    if not uid:
        print("\n  !! no account to test with; everything after this is "
              "meaningless.\n")
        return 1

    s, b = fresh_login(addr, TEMP)
    ck("it signs in with the temporary password",
       s["t"] is not None, f"{b}")
    st, b = rq(s, "POST", "/api/auth/change-password",
               {"current_password": TEMP, "new_password": PW})
    ck("and sets a real password", st == 200, f"{st} {b}")

    # --------------------------------------------------------- enrollment
    print("\n-- enrollment is not complete until a code is proved --")
    st, body0 = rq(s, "POST", "/api/auth/2fa/enroll", {})
    ck("enroll returns an otpauth:// uri",
       st == 200 and str(body0.get("uri", "")).startswith("otpauth://totp/"),
       f"{st} {str(body0)[:120]}")
    ck("and the key in groups of four, for typing",
       len((body0.get("secret_groups") or "").split(" ")) == 8,
       body0.get("secret_groups", ""))
    secret = secret_from(body0)

    st, b = rq(s, "POST", "/api/auth/2fa/confirm", {"code": "000000"})
    ck("a wrong code does not complete it", st == 400, f"{st} {b}")
    st, status = rq(s, "GET", "/api/auth/2fa")
    ck("and the account still reads as not enrolled",
       status.get("enrolled") is False, str(status.get("enrolled")))

    st, conf = rq(s, "POST", "/api/auth/2fa/confirm",
                  {"code": auth.totp_code(secret)})
    ck("a correct code completes it", st == 200, f"{st} {conf}")
    codes = conf.get("recovery_codes") or []
    ck(f"{RECOVERY_TOTAL} recovery codes are returned, once",
       len(codes) == RECOVERY_TOTAL, str(len(codes)))
    st, status = rq(s, "GET", "/api/auth/2fa")
    ck("the account now reads as enrolled", status.get("enrolled") is True)
    ck("and counts the recovery codes",
       status.get("recovery_codes_left") == RECOVERY_TOTAL,
       str(status.get("recovery_codes_left")))
    st, again = rq(s, "GET", "/api/auth/2fa")
    ck("the recovery codes are not readable afterwards",
       not any("recovery_code" == k or "recovery_codes" == k for k in again),
       ", ".join(sorted(again)))

    # ------------------------------------------------------- the challenge
    print("\n-- a correct password now buys a challenge, not a session --")
    s2, b2 = fresh_login(addr, PW)
    ck("the response carries a challenge", "twofa" in b2, f"{list(b2)}")
    ck("and no session token", not b2.get("csrf_token"), str(list(b2)))
    ck("no session cookie was set", cookie(s2, auth.SESSION_COOKIE) is None)
    ck("the challenge names the authenticator method",
       (b2.get("twofa") or {}).get("method") == "totp", str(b2.get("twofa")))
    chal = (b2.get("twofa") or {}).get("challenge")
    ck("the challenge token is not the session cookie by another name",
       bool(chal) and chal != cookie(s2, auth.SESSION_COOKIE))

    st, vb = rq(s2, "POST", "/api/auth/2fa/verify",
                {"challenge": chal, "code": "000000"})
    ck("a wrong code is refused", st == 400, f"{st} {vb}")
    # 400 rather than 401 on purpose: `request()` in index.html calls
    # onAuthLost() on any 401, which would tear down the very screen that is
    # about to accept the right code. See CLAUDE.md.
    ck("a merely wrong code leaves the challenge alive",
       vb.get("restart") is not True, str(vb))
    ck("and it says how many attempts are left",
       vb.get("attempts_left") == MAX_ATTEMPTS - 1, str(vb))

    st, vb = rq(s2, "POST", "/api/auth/2fa/verify",
                {"challenge": chal, "code": auth.totp_code(secret)})
    ck("the right code returns a session", st == 200 and vb.get("csrf_token"),
       f"{st} {vb}")
    s2["t"] = vb.get("csrf_token")
    ck("a session cookie is set now",
       cookie(s2, auth.SESSION_COOKIE) is not None)
    st, b = rq(s2, "GET", "/api/systems")
    ck("and the session really works", st == 200, f"{st}")
    st, vb2 = rq(s2, "POST", "/api/auth/2fa/verify",
                 {"challenge": chal, "code": auth.totp_code(secret)})
    ck("the challenge cannot be spent twice", st == 400, f"{st} {vb2}")

    print(f"\n-- {MAX_ATTEMPTS} wrong answers kill the challenge itself --")
    s3, b3 = fresh_login(addr, PW)
    chal3 = (b3.get("twofa") or {}).get("challenge")
    last = {}
    for _ in range(MAX_ATTEMPTS):
        st, last = rq(s3, "POST", "/api/auth/2fa/verify",
                      {"challenge": chal3, "code": "111111"})
    ck("the last wrong answer is refused", st == 400, f"{st} {last}")
    st, bb = rq(s3, "POST", "/api/auth/2fa/verify",
                {"challenge": chal3, "code": auth.totp_code(secret)})
    ck("and a CORRECT code afterwards is refused too", st == 400, f"{st} {bb}")
    ck("the dead challenge asks for a restart rather than a retry",
       bb.get("restart") is True, str(bb))

    # ---------------------------------------------------- recovery codes
    print("\n-- a recovery code signs in once --")
    s4, b4 = fresh_login(addr, PW)
    st, rb = rq(s4, "POST", "/api/auth/2fa/verify",
                {"challenge": (b4.get("twofa") or {}).get("challenge"),
                 "code": codes[0]})
    ck("it is accepted", st == 200 and rb.get("csrf_token"), f"{st} {rb}")
    ck("and reports how many remain",
       rb.get("recovery_codes_left") == RECOVERY_TOTAL - 1,
       str(rb.get("recovery_codes_left")))
    s5, b5 = fresh_login(addr, PW)
    st, rb = rq(s5, "POST", "/api/auth/2fa/verify",
                {"challenge": (b5.get("twofa") or {}).get("challenge"),
                 "code": codes[0]})
    ck("the same one is refused the second time", st == 400, f"{st} {rb}")

    print("\n-- replacing the recovery codes needs the password --")
    st, b = rq(s2, "POST", "/api/auth/2fa/recovery-codes",
               {"password": "not-the-password"})
    ck("a wrong password is refused", st == 400, f"{st} {b}")
    st, b = rq(s2, "POST", "/api/auth/2fa/recovery-codes", {"password": PW})
    ck("the right one issues a fresh set", st == 200
       and len(b.get("recovery_codes") or []) == RECOVERY_TOTAL, f"{st}")
    newcodes = b.get("recovery_codes") or []
    ck("the new codes are not the old ones",
       bool(newcodes) and not set(newcodes) & set(codes))
    s6, b6 = fresh_login(addr, PW)
    st, rb = rq(s6, "POST", "/api/auth/2fa/verify",
                {"challenge": (b6.get("twofa") or {}).get("challenge"),
                 "code": codes[1]})
    ck("an unused code from the OLD set no longer works", st == 400,
       f"{st} {rb}")
    codes = newcodes

    # -------------------------------------------------- trusted devices
    print("\n-- remembering a device skips the second factor --")
    s7, b7 = fresh_login(addr, PW)
    st, rb = rq(s7, "POST", "/api/auth/2fa/verify",
                {"challenge": (b7.get("twofa") or {}).get("challenge"),
                 "code": auth.totp_code(secret), "remember": True})
    ck("verify with remember succeeds", st == 200, f"{st} {rb}")
    s7["t"] = rb.get("csrf_token")
    ck("a device cookie is issued",
       cookie(s7, auth.TRUSTED_DEVICE_COOKIE) is not None)
    # The SAME session, so the jar still holds the device cookie. A fresh
    # session here would prove nothing — that is the whole mechanism.
    st, b = rq(s7, "POST", "/api/auth/login", {"email": addr, "password": PW})
    ck("the next sign-in from it needs no code",
       st == 200 and "twofa" not in b and b.get("csrf_token"), f"{st} {list(b)}")
    s7["t"] = b.get("csrf_token")
    st, status = rq(s7, "GET", "/api/auth/2fa")
    ck("the device is listed so it can be revoked",
       len(status.get("trusted_devices") or []) >= 1,
       str(status.get("trusted_devices")))
    ck("the listing carries no usable token",
       not any(cookie(s7, auth.TRUSTED_DEVICE_COOKIE) == d.get("id")
               for d in (status.get("trusted_devices") or [])))

    print("\n-- a recovery code earns no remembered device --")
    s8, b8 = fresh_login(addr, PW)
    st, rb = rq(s8, "POST", "/api/auth/2fa/verify",
                {"challenge": (b8.get("twofa") or {}).get("challenge"),
                 "code": codes[0], "remember": True})
    ck("the recovery sign-in succeeds", st == 200, f"{st} {rb}")
    ck("but no device cookie is issued",
       cookie(s8, auth.TRUSTED_DEVICE_COOKIE) is None)

    print("\n-- revoking stops the skipping --")
    st, b = rq(s7, "POST", "/api/auth/devices", {})
    ck("revoke reports what it revoked", st == 200 and b.get("revoked", 0) >= 1,
       f"{st} {b}")
    st, b = rq(s7, "POST", "/api/auth/login", {"email": addr, "password": PW})
    ck("the same browser is challenged again", st == 200 and "twofa" in b,
       f"{st} {list(b)}")

    print("\n-- changing the password revokes remembered devices --")
    s9, b9 = fresh_login(addr, PW)
    st, rb = rq(s9, "POST", "/api/auth/2fa/verify",
                {"challenge": (b9.get("twofa") or {}).get("challenge"),
                 "code": auth.totp_code(secret), "remember": True})
    s9["t"] = rb.get("csrf_token")
    ck("a device is remembered again", st == 200
       and cookie(s9, auth.TRUSTED_DEVICE_COOKIE) is not None, f"{st}")
    PW2 = "Lantern-Tundra-57#k"
    st, b = rq(s9, "POST", "/api/auth/change-password",
               {"current_password": PW, "new_password": PW2})
    ck("the password change is accepted", st == 200, f"{st} {b}")
    st, b = rq(s9, "POST", "/api/auth/login", {"email": addr, "password": PW2})
    ck("and the remembered browser is challenged once more",
       st == 200 and "twofa" in b, f"{st} {list(b)}")
    PW = PW2

    # ------------------------------------------------------------ disable
    print("\n-- turning your own second factor off needs the password --")
    s10, b10 = fresh_login(addr, PW)
    st, rb = rq(s10, "POST", "/api/auth/2fa/verify",
                {"challenge": (b10.get("twofa") or {}).get("challenge"),
                 "code": auth.totp_code(secret)})
    s10["t"] = rb.get("csrf_token")
    st, b = rq(s10, "POST", "/api/auth/2fa/disable",
               {"password": "not-the-password"})
    ck("a wrong password is refused", st == 400, f"{st} {b}")
    st, status = rq(s10, "GET", "/api/auth/2fa")
    ck("and nothing was turned off", status.get("enrolled") is True)
    st, b = rq(s10, "POST", "/api/auth/2fa/disable", {"password": PW})
    ck("the right password turns it off", st == 200, f"{st} {b}")
    st, status = rq(s10, "GET", "/api/auth/2fa")
    ck("the account reads as not enrolled", status.get("enrolled") is False)
    ck("and keeps no recovery codes",
       (status.get("recovery_codes_left") or 0) == 0,
       str(status.get("recovery_codes_left")))
    s11, b11 = fresh_login(addr, PW)
    ck("signing in needs only the password again",
       "twofa" not in b11 and b11.get("csrf_token"), f"{list(b11)}")
    st, rb = rq(s11, "POST", "/api/auth/2fa/verify",
                {"challenge": "invented", "code": "123456"})
    ck("an invented challenge is refused with restart",
       st == 400 and rb.get("restart") is True, f"{st} {rb}")

    # ---------------------------------------------------- emailed codes
    if not sink:
        print("\n-- emailed codes: SKIPPED, EMAIL_SINK_PATH is not set --")
        print("   The code has to be read out of the message, so this section\n"
              "   needs the server's mail sink. Nothing else is affected.")
    else:
        print("\n-- an emailed code is an alternative, not a replacement --")
        secret, codes = enroll(s11)
        ck("enrolled again for this section", bool(secret))
        st, b = rq(boss, "PUT", "/api/preferences",
                   {"values": {"allow_email_2fa": "true"}})
        ck("allow_email_2fa saved", st == 200, f"{st} {b}")

        def tail():
            try:
                lines = [json.loads(l) for l in open(sink, encoding="utf-8")
                         if l.strip()]
            except FileNotFoundError:
                return None
            return lines[-1] if lines else None

        was = tail()
        s12, b12 = fresh_login(addr, PW)
        tw = b12.get("twofa") or {}
        ck("an enrolled account is still offered the app first",
           tw.get("method") == "totp", str(tw))
        ck("with email listed as an alternative",
           "email" in (tw.get("methods") or []), str(tw.get("methods")))
        st, rb = rq(s12, "POST", "/api/auth/2fa/resend",
                    {"challenge": tw.get("challenge")})
        ck("asking for one reports success", st == 200, f"{st} {rb}")
        msg = tail()
        ck("a message really was written", msg is not None and msg != was,
           str(msg)[:80])
        m = re.search(r"\b(\d{6})\b", (msg or {}).get("body", ""))
        ck("it carries a six-digit code", bool(m),
           (msg or {}).get("body", "")[:90])
        if m:
            ck("the code is not in the response that triggered it",
               m.group(1) not in json.dumps(rb))
            st, vb = rq(s12, "POST", "/api/auth/2fa/verify",
                        {"challenge": tw.get("challenge"), "code": m.group(1)})
            ck("and it signs in", st == 200 and vb.get("csrf_token"),
               f"{st} {vb}")
            ck("the code is not echoed back either",
               m.group(1) not in json.dumps(vb))
            st, vb = rq(s12, "POST", "/api/auth/2fa/verify",
                        {"challenge": tw.get("challenge"), "code": m.group(1)})
            ck("the same emailed code cannot be reused", st == 400, f"{st}")

        # The behavior this section exists to pin down, and it is not obvious
        # from either switch on its own: with emailed codes allowed, somebody
        # with NO authenticator is challenged by email rather than told to
        # enroll — because whether email works is a fact about the deployment,
        # not about the person (migration 0023). Ten assertions later in this
        # suite were written on the opposite assumption and failed, which is
        # how it got checked here.
        st, b = rq(boss, "POST", f"/api/users/{uid}/2fa-reset", {})
        ck("the account's authenticator is cleared", st == 200, f"{st} {b}")
        s12b, b12b = fresh_login(addr, PW)
        twb = b12b.get("twofa") or {}
        ck("an account with no app is still challenged, by email",
           twb.get("method") == "email", str(twb))
        ck("and email is the only method offered",
           (twb.get("methods") or []) == ["email"], str(twb.get("methods")))

        # Put it back before the next section, which asserts that an account
        # with no factor signs in on the password alone — true only when
        # emailed codes are off. A section that leaves a deployment-wide
        # switch flipped makes the next one test something else.
        st, b = rq(boss, "PUT", "/api/preferences",
                   {"values": {"allow_email_2fa": "false"}})
        ck("emailed codes can be turned off again", st == 200, f"{st} {b}")

    # -------------------------------------------- forced enrollment (gated)
    if not gate:
        print("\n-- forced enrollment: SKIPPED, set TWOFA_GATE=yes to run it --")
        print("   It turns require_2fa on for the whole deployment for about\n"
              "   ten requests, which means every signed-in person is asked to\n"
              "   enroll in that window. The restore is in a finally, so this\n"
              "   is safe — but it is somebody else's app, so it is opt-in.")
    elif not boss_enrolled:
        print("\n-- forced enrollment: SKIPPED, the borrowed account has no "
              "second factor --")
        print("   This is a refusal rather than a limitation. `require_2fa` is\n"
              "   deployment-wide, so turning it on puts EVERY account into\n"
              "   pending-enrollment — including the one this suite signed in\n"
              "   with. An un-enrolled borrowed account would then be refused\n"
              "   Preferences, so the restore at the end could not run and the\n"
              "   suite would leave the requirement switched on. It would also\n"
              "   be the one thing this suite must never do: change how a real\n"
              "   person signs in to their own app.\n"
              "   Enroll the account you are signing in as, then re-run.")
    else:
        print("\n-- require_2fa makes enrollment compulsory --")
        st, b = rq(boss, "PUT", "/api/preferences",
                   {"values": {"require_2fa": "true"}})
        ck("the requirement is accepted while a factor can work",
           st == 200, f"{st} {b}")
        # This account was left un-enrolled by the disable section above, or
        # re-enrolled by the email one. Either way, clear it first so the
        # forced-enrollment path is what gets exercised.
        rq(boss, "POST", f"/api/users/{uid}/2fa-reset", {})
        s13, b13 = fresh_login(addr, PW)
        ck("the password alone still signs in — there is nothing to ask for",
           b13.get("csrf_token") is not None, f"{list(b13)}")
        ck("but the account is flagged for enrollment",
           ((b13.get("user") or {}).get("must_enroll_2fa")) is True,
           str(b13.get("user")))
        st, b = rq(s13, "GET", "/api/systems")
        ck("the application is closed until they enroll",
           st == 403 and b.get("must_enroll_2fa"), f"{st} {b}")
        st, b = rq(s13, "GET", "/api/auth/2fa")
        ck("but the enrollment screen's own endpoint is reachable",
           st == 200, f"{st} {b}")
        sec2, _ = enroll(s13)
        ck("enrollment completes", bool(sec2))
        st, b = rq(s13, "GET", "/api/systems")
        ck("and the application opens", st == 200, f"{st} {b}")
        st, b = rq(s13, "POST", "/api/auth/2fa/disable", {"password": PW})
        ck("it cannot be turned off while the organization requires it",
           st == 400 and "requires" in str(b.get("error", "")), f"{st} {b}")
        # Put it back here as well as in the finally, so the sections after
        # this one are not reading a deployment this one changed.
        st, b = rq(boss, "PUT", "/api/preferences",
                   {"values": {"require_2fa": "false"}})
        ck("the requirement can be turned off again", st == 200, f"{st} {b}")

    # ----------------------------------------------- administrator reset
    print("\n-- an administrator can clear somebody's second factor --")
    st, b = rq(boss, "POST", f"/api/users/{uid}/2fa-reset", {})
    ck("the reset is accepted", st == 200, f"{st} {b}")
    s14, b14 = fresh_login(addr, PW)
    ck("the account signs in with the password alone again",
       "twofa" not in b14 and b14.get("csrf_token"), f"{list(b14)}")
    st, status = rq(s14, "GET", "/api/auth/2fa")
    ck("and reads as not enrolled", status.get("enrolled") is False)

    if boss_role != "SuperAdmin":
        print("\n-- outranking: SKIPPED, the borrowed account is not a "
              "SuperAdmin --")
    else:
        print("\n-- but not somebody who outranks them --")
        st, b = rq(boss, "PUT", f"/api/users/{uid}", {"role": "Administrator"})
        ck("the test account is promoted to Administrator", st == 200,
           f"{st} {b}")
        s15, b15 = fresh_login(addr, PW)
        st, b = rq(s15, "POST", f"/api/users/{boss_id}/2fa-reset", {})
        ck("clearing a SuperAdmin's factor is refused", st == 403,
           f"{st} {b}")

    print("\n-- nothing secret leaves by a route it should not --")
    st, status = rq(s14, "GET", "/api/auth/2fa")
    blob = json.dumps(status)
    ck("the status endpoint carries no key material",
       "totp_salt" not in blob and "secret" not in blob, blob[:160])
    st, users = rq(boss, "GET", "/api/users")
    ublob = json.dumps(users)
    ck("the user list carries no enrollment secrets",
       "totp_salt" not in ublob and "secret_groups" not in ublob)
    ck("nor any recovery code", "recovery_code" not in ublob)

    return 0


if __name__ == "__main__":
    rc = main()
    # A precondition returns 2 and means "did not run"; otherwise the exit
    # status is about the checks, which is what the checklist reads.
    sys.exit(rc if rc else (1 if FAILED else 0))
