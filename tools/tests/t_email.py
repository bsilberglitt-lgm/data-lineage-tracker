"""Email configuration and self-service password reset (0.9.0).

    EMAIL_SINK_PATH=/tmp/h2/mail.jsonl      # the server needs both of these
    EMAIL_SINK_CONFIRM=discard-all-mail
    python3 tools/tests/t_email.py 8900 --email you@example.gov

**No mail leaves this suite.** The server must be started with
`EMAIL_SINK_PATH` pointing at a file, which makes `mailer.send` append each
message as JSON instead of delivering it. A test that could email a real
person is not one anybody should run twice, and the sink is also the only way
to read a token out of a message the suite never receives. Without it this
suite says so and exits, rather than either mailing somebody or reporting
failures that are really a missing environment variable.

**The sink needs `EMAIL_SINK_CONFIRM=discard-all-mail` as well**, and the
server refuses to start on the path alone. That is not ceremony: a single
variable that makes password reset report success and deliver nothing travels
in any environment file copied from a test deployment, and the symptom appears
much later as users who never got their mail. Two variables mean one of them
is a decision. This suite checks both, because a missing confirmation would
otherwise show up as a server that would not start.

**A credential is needed too, even though nothing can leave.** `mailer.send`
evaluates `problems()` before it looks at the sink, on purpose, so a test
environment cannot mask a misconfigured transport. Any value will do for a
local run — `SMTP_PASSWORD=not-a-real-password` — and the suite stops with a
single message if mail is not configured rather than failing every check that
depends on it.

**The reset is performed on an account this suite owns, never on the one it
signed in with.** That distinction is the whole point of `_client.py`: a suite
borrows a real person's account to authenticate and must never change its
password. A reset is one-way — there is no putting it back — so running it on
the borrowed account would lock Ben out of his own local app. `ensure_account`
creates `resetme+<clock>@...`, the suite resets *that*, and the borrowed
session is only ever used to configure settings and read the audit log.

What it protects, each a decision rather than an accident:

  * **A request answers identically for an address with an account, one
    without, and a deactivated one.** Otherwise the endpoint is a free list of
    who works at the department, which for a state agency is a phishing target
    list. Asserted as equality of the whole response, not as "both succeeded".
  * **The link is built from `app_base_url`, never the Host header.** The
    suite sends a forged Host and asserts the link ignores it. Reading the
    header is the standard way reset links get poisoned: the victim receives a
    real token pointing at the attacker's server.
  * **Single use**, because a link lives in an inbox and inboxes get forwarded.
  * **Expiry**, by aging the row — the only way to test an hour in a test.
  * **Spending one link kills the others** for that account.
  * **A reset revokes every session**, with no exception for the one asking:
    unlike change-password, nobody is signed in here.
  * **The new password goes through the same policy** as any other.
  * **The credential never appears in any API response.**

A suite borrows the database and does not own it. This one changes
deployment-wide preferences, which is as intrusive as a suite here gets, so it
records every email setting it found and restores all of them at the end, with
a closing check to prove it.
"""
import datetime
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _client import (session, request as rq, sign_in_with, ensure_account,  # noqa: E402
                     unique_email, report, BASE)

PASSED, FAILED = [], []


def ck(label, cond, detail=""):
    (PASSED if cond else FAILED).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}"
          + (f"   [{detail}]" if not cond and detail else ""))


# Mirrors auth.RESET_RATE_LIMIT. Written out rather than imported because no
# suite may import the app or its modules (see CLAUDE.md) — but asserted
# exactly, so a change to the limit fails here and has to be made on purpose.
RESET_RATE_LIMIT = 5

EMAIL_KEYS = ["email_transport", "email_from_address", "email_from_name",
              "app_base_url", "smtp_host", "smtp_port", "smtp_security",
              "smtp_username", "enable_self_service_reset"]


def main():
    sink = (os.environ.get("EMAIL_SINK_PATH") or "").strip()
    if not sink:
        print("\nEMAIL_SINK_PATH is not set.\n"
              "  This suite must not be able to send real email, so it needs\n"
              "  the server started with a sink:\n"
              "    EMAIL_SINK_PATH=/tmp/h2/mail.jsonl \\\n"
              "    EMAIL_SINK_CONFIRM=discard-all-mail python3 app.py\n"
              "  and the same values exported for this suite.\n")
        return 2
    # Checked here as well as by the server, because the server's answer to a
    # missing confirmation is to refuse to start — which from in here is a
    # connection error, and a connection error names nothing.
    if (os.environ.get("EMAIL_SINK_CONFIRM") or "").strip() != "discard-all-mail":
        print("\nEMAIL_SINK_CONFIRM is not set to discard-all-mail.\n"
              "  The sink needs both variables, and the server refuses to\n"
              "  start on the path alone. Export it here too:\n"
              "    export EMAIL_SINK_CONFIRM=discard-all-mail\n")
        return 2

    def count():
        if not os.path.exists(sink):
            return 0
        with open(sink, encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())

    def since(mark):
        """Messages appended after the `mark`th line. The sink is append-only,
        so a count taken before an action is a cursor into it."""
        if not os.path.exists(sink):
            return []
        with open(sink, encoding="utf-8") as fh:
            lines = [l for l in fh if l.strip()]
        return [json.loads(l) for l in lines[mark:]]

    def token_in(message):
        return message["body"].split("token=")[1].split()[0].strip()

    boss = sign_in_with(session(), rq)

    # -------------------------------------------------------------- settings
    print("-- every email setting exists, and is noted so it can be put back --")
    st, prefs = rq(boss, "GET", "/api/preferences")
    before = {p["key"]: p["value"] for p in prefs}
    absent = [k for k in EMAIL_KEYS if k not in before]
    ck("the Email settings are all present", not absent, ", ".join(absent))
    restore = {k: (before.get(k) or "") for k in EMAIL_KEYS}

    # Everything from here is wrapped so the restore below ALWAYS runs.
    # Without it, any crash leaves this deployment's email preferences set
    # to the suite's test values -- which is what an IndexError on an empty
    # sink did once, contradicting this suite's own docstring. A suite that
    # borrows the database has to put it back on the way out as well as on
    # the way through.
    try:

        print("-- the credential never crosses the wire --")
        st, desc = rq(boss, "GET", "/api/email/test")
        ck("a SuperAdmin can read the status", st == 200, str(st))
        ck("it names the environment variable to set",
           "secret_env_var" in desc, json.dumps(desc)[:160])
        secret = os.environ.get("SMTP_PASSWORD") or ""
        ck("and the password itself is not in the response",
           not secret or secret not in json.dumps(desc), "it appeared")
        ck("nor in the preferences",
           not secret or secret not in json.dumps(prefs), "it appeared")

        print("-- a setting that chooses a code path is validated on write --")
        for key, value, what in [
                ("email_transport", "../../etc/passwd", "transport"),
                ("smtp_security", "plaintext-please", "encryption"),
                ("app_base_url", "lineage.example.gov", "address with no scheme")]:
            st, err = rq(boss, "PUT", "/api/preferences", {"values": {key: value}})
            ck(f"a bad {what} is refused", st == 400,
               f"{st} {json.dumps(err)[:80]}")

        print("-- configuring email --")
        st, _ = rq(boss, "PUT", "/api/preferences", {"values": {
            "email_transport": "smtp", "smtp_host": "relay.example.gov",
            "smtp_port": "587", "smtp_security": "starttls",
            "smtp_username": "lineage",
            "email_from_address": "lineage@example.gov",
            "email_from_name": "Data Lineage Tracker",
            "app_base_url": "https://lineage.example.gov",
            "enable_self_service_reset": "true"}})
        ck("the settings save", st == 200, str(st))
        st, desc = rq(boss, "GET", "/api/email/test")
        ck("and it reports itself ready to send",
           desc.get("configured") is True, json.dumps(desc))
        ck("with the sink active, so nothing can really leave",
           desc.get("sink_active") is True, json.dumps(desc))

        # Stop here rather than running sixty checks that cannot pass.
        #
        # Every check below this point depends on mail being configured, so one
        # missing variable otherwise produces a screen of failures and then an
        # `IndexError` on the first read of an empty sink -- the cause reported
        # everywhere except where it is, which is the most expensive failure
        # shape this project keeps finding. Ben lost a round to exactly that.
        #
        # The credential is the usual culprit, and it is needed even though
        # nothing can leave: `mailer.send` evaluates `problems()` **before** it
        # looks at the sink, deliberately, so that a test environment cannot
        # mask a misconfigured transport.
        if desc.get("configured") is not True:
            print("\n  Email is not configured, so nothing below can pass:")
            for problem in desc.get("problems") or []:
                print(f"    - {problem}")
            print("\n  For a local run the credential can be anything at all,"
                  "\n  because the sink intercepts before any socket is opened:"
                  "\n"
                  "\n    docker compose down"
                  "\n    EMAIL_SINK_PATH=/app/.mail-test.jsonl \\"
                  "\n    EMAIL_SINK_CONFIRM=discard-all-mail \\"
                  "\n    SMTP_PASSWORD=not-a-real-password docker compose up -d"
                  "\n"
                  "\n  The container's environment is fixed when it is created,"
                  "\n  so it has to be recreated rather than restarted.\n")
            return 2

        # ----------------------------------------------- the suite's own account
        print("-- an account this suite owns, so the reset harms nobody --")
        victim_email = unique_email("resetme")
        # The names and the temporary password must not share a word: the policy
        # refuses a password containing the account's own name, and `ensure_account`
        # hands back a plausible password either way — so "Reset Me" with
        # "Temp-Pw-For-Reset-1!" silently created nobody and every later check was
        # about an account that did not exist.
        st, victim, victim_pw = ensure_account(
            boss, rq, victim_email, "Quill", "Barrowman", "Viewer",
            "Zt7-whistle-Marmalade-4!")
        # Asserted on the session rather than on the password, which is truthy
        # whatever happened.
        ck("the account exists and is signed in", bool(victim.get("t")),
           f"{st} {victim_email}")

        # ------------------------------------------------- the anonymous surface
        print("-- what the sign-in screen is told, and what it is not --")
        anon = session()
        st, pub = rq(anon, "GET", "/api/branding/public")
        ck("the reset link is offered",
           pub.get("self_service_reset") is True, json.dumps(pub))
        leaked = [k for k in pub if k.startswith(("smtp", "email_"))]
        ck("and no mail setting is public", not leaked, ", ".join(leaked))

        print("-- a real address and an unknown one answer identically --")
        mark = count()
        unknown = rq(anon, "POST", "/api/auth/forgot-password",
                     {"email": "definitely-nobody@example.gov"})
        known = rq(anon, "POST", "/api/auth/forgot-password",
                   {"email": victim_email})
        ck("same status and same body", unknown == known, f"{unknown} vs {known}")
        sent = since(mark)
        ck("and exactly one message was sent", len(sent) == 1,
           json.dumps([m["to"] for m in sent]))
        ck("to the address that has an account",
           sent and sent[0]["to"] == victim_email, json.dumps(sent[:1]))

        print("-- the link comes from the setting, not from the Host header --")
        mark = count()
        rq(anon, "POST", "/api/auth/forgot-password", {"email": victim_email},
           extra_headers={"Host": "evil.example.net",
                          "X-Forwarded-Host": "evil.example.net"})
        sent = since(mark)
        body = sent[-1]["body"] if sent else ""
        ck("the link uses the configured address",
           "https://lineage.example.gov/reset-password#token=" in body, body[:150])
        # The token is in the fragment so it is never sent to the server and so
        # cannot reach a proxy's access log. Asserted as the absence of the query
        # form, because reverting `reset_link` would still satisfy a check that
        # only looked for "token=" somewhere in the body.
        ck("the token is in the fragment, not the query string",
           "/reset-password?token=" not in body, body[:150])
        ck("and the forged host appears nowhere in the message",
           "evil.example.net" not in body, body[:200])
        ck("the from address is the configured one",
           sent and sent[-1]["from"] == "lineage@example.gov",
           json.dumps(sent[-1:])[:120])
        first_token = token_in(sent[-1])
        ck("a token was issued", len(first_token) > 20, repr(first_token[:10]))

        print("-- a deactivated account is silent, and still indistinguishable --")
        st, users = rq(boss, "GET", "/api/users")
        row = [u for u in users if u["email"] == victim_email]
        ck("the account is visible to a SuperAdmin", len(row) == 1, str(len(row)))
        uid = row[0]["user_id"]
        rq(boss, "PUT", f"/api/users/{uid}", {"is_active": 0})
        mark = count()
        off = rq(anon, "POST", "/api/auth/forgot-password", {"email": victim_email})
        ck("it answers like every other request", off == known, f"{off} vs {known}")
        ck("and no mail was sent", since(mark) == [], json.dumps(since(mark)))
        rq(boss, "PUT", f"/api/users/{uid}", {"is_active": 1})

        print("-- a token nobody issued, and a password that breaks the policy --")
        st, _ = rq(anon, "POST", "/api/auth/reset-password",
                   {"token": "not-a-real-token", "new_password": "Str0ng-Pw-2026!"})
        ck("an unknown token is refused", st == 400, str(st))
        st, err = rq(anon, "POST", "/api/auth/reset-password",
                     {"token": first_token, "new_password": "short"})
        ck("a weak password is refused", st == 400, str(st))
        ck("and says why, field by field", bool(err.get("problems")),
           json.dumps(err)[:140])

        print("-- expiry, by aging the row rather than waiting an hour --")
        db = os.environ.get("FAKE_SQLITE") or "/tmp/h2/test.db"
        if os.path.exists(db):
            digest = hashlib.sha256(first_token.encode("utf-8")).hexdigest()
            past = (datetime.datetime.utcnow()
                    - datetime.timedelta(hours=2)).isoformat()
            c = sqlite3.connect(db)
            cur = c.execute("UPDATE password_reset_token SET expires_date=? "
                            "WHERE token_hash=?", (past, digest))
            c.commit()
            moved = cur.rowcount
            c.close()
            ck("the token is stored as a hash, so the row was found by one",
               moved == 1, f"{moved} rows matched the sha256 of the token")
            st, err = rq(anon, "POST", "/api/auth/reset-password",
                         {"token": first_token,
                          "new_password": "Str0ng-Pw-2026!"})
            ck("an expired link is refused", st == 400, str(st))
            ck("and says it expired rather than that it was never valid",
               "expired" in str(err.get("error", "")).lower(),
               json.dumps(err)[:140])
        else:
            print(f"   (skipped: no SQLite file at {db})")

        print("-- a fresh link, spent exactly once --")
        mark = count()
        rq(anon, "POST", "/api/auth/forgot-password", {"email": victim_email})
        fresh = token_in(since(mark)[-1])
        ck("a new request issues a different token", fresh != first_token,
           "the same token came back twice")
        new_pw = "Reset-By-Suite-2026!"
        st, _ = rq(anon, "POST", "/api/auth/reset-password",
                   {"token": fresh, "new_password": new_pw})
        ck("the reset succeeds", st == 200, str(st))
        st, err = rq(anon, "POST", "/api/auth/reset-password",
                     {"token": fresh, "new_password": "Another-Pw-2026!"})
        ck("the same link cannot be used twice", st == 400, str(st))
        ck("and says it was already used, not that it is invalid",
           "already" in str(err.get("error", "")).lower(), json.dumps(err)[:140])

        print("-- the reset revoked the account's own session --")
        st, _ = rq(victim, "GET", "/api/auth/me")
        ck("the session it held before the reset is dead", st == 401, str(st))

        print("-- the new password works and the temporary one does not --")
        back = session()
        st, d = rq(back, "POST", "/api/auth/login",
                   {"email": victim_email, "password": new_pw})
        ck("the new password signs in", st == 200, str(st))
        if st == 200:
            back["t"] = d.get("csrf_token")
        ck("and the reset cleared the must-change flag, so it is a real password",
           st == 200 and not (d.get("user") or {}).get("must_change_password"),
           json.dumps(d.get("user") or {})[:120])
        stale = session()
        st, _ = rq(stale, "POST", "/api/auth/login",
                   {"email": victim_email, "password": victim_pw})
        ck("the old temporary password does not", st == 401, str(st))

        print("-- spending one link kills the others for that account --")
        mark = count()
        rq(anon, "POST", "/api/auth/forgot-password", {"email": victim_email})
        rq(anon, "POST", "/api/auth/forgot-password", {"email": victim_email})
        two = since(mark)
        ck("two links were issued", len(two) == 2, str(len(two)))
        older, newer = token_in(two[0]), token_in(two[1])
        final_pw = "Final-Pw-For-Suite-2026!"
        st, _ = rq(anon, "POST", "/api/auth/reset-password",
                   {"token": newer, "new_password": final_pw})
        ck("the newer link works", st == 200, str(st))
        st, err = rq(anon, "POST", "/api/auth/reset-password",
                     {"token": older, "new_password": "Yet-Another-2026!"})
        ck("and the older one is dead rather than still usable", st == 400,
           f"{st} {json.dumps(err)[:80]}")

        print("-- the rate limit caps the mail without changing the answer --")
        # A FRESH account, because the one above has already spent its hour's
        # allowance several times over. Run against it, the burst produces zero
        # messages and `burst < 8` passes while proving nothing at all — the cap
        # could be anywhere, including one. Starting from an empty window is what
        # makes the number mean something.
        burst_email = unique_email("ratelimit")
        st, burst_sess, _bpw = ensure_account(
            boss, rq, burst_email, "Quill", "Barrowman", "Viewer",
            "Zt7-whistle-Marmalade-4!")
        ck("a fresh account for the burst", bool(burst_sess.get("t")), str(st))
        mark = count()
        answers = [rq(anon, "POST", "/api/auth/forgot-password",
                      {"email": burst_email}) for _ in range(8)]
        ck("every answer is identical", len({str(a) for a in answers}) == 1,
           json.dumps([a[0] for a in answers]))
        burst = len(since(mark))
        ck("the mail is capped below the number of requests", burst < 8,
           f"{burst} messages for 8 requests")
        ck("and capped at the documented limit, not somewhere else",
           burst == RESET_RATE_LIMIT,
           f"{burst} messages, expected {RESET_RATE_LIMIT}")
        print(f"   8 requests produced {burst} messages")

        print("-- it is all in the audit log --")
        st, audit = rq(boss, "GET", "/api/audit?limit=100")
        events = audit.get("events") if isinstance(audit, dict) else audit
        actions = [e["action"] for e in (events or [])]
        ck("the request is recorded", "password_reset_requested" in actions,
           ", ".join(sorted(set(actions)))[:160])
        ck("and so is the reset itself", "password_reset" in actions,
           ", ".join(sorted(set(actions)))[:160])
        resets = [e for e in (events or []) if e["action"] == "password_reset"]
        ck("with no signed-in actor, because there was not one",
           bool(resets) and not resets[0].get("actor_user_id"),
           json.dumps(resets[:1])[:160])

        print("-- with the feature off, nothing is offered and nothing works --")
        st, _ = rq(boss, "PUT", "/api/preferences",
                   {"values": {"enable_self_service_reset": "false"}})
        st, pub = rq(anon, "GET", "/api/branding/public")
        ck("the sign-in screen stops offering it",
           pub.get("self_service_reset") is False, json.dumps(pub))
        st, _ = rq(anon, "POST", "/api/auth/forgot-password",
                   {"email": victim_email})
        ck("and a crafted request is refused", st == 404, str(st))

        print("-- a transport with no credential is reported, not hidden --")
        rq(boss, "PUT", "/api/preferences",
           {"values": {"email_transport": "resend"}})
        st, desc = rq(boss, "GET", "/api/email/test")
        ck("switching transport without its key is not ready",
           desc.get("configured") is False and desc.get("secret_present") is False,
           json.dumps(desc))
        ck("and it names the variable to set",
           desc.get("secret_env_var") == "RESEND_API_KEY", json.dumps(desc))

        print("-- only a SuperAdmin may read or test the mail settings --")
        # A fresh sign-in, not the `back` session from earlier: the second reset
        # revoked every session this account had, which is the feature working.
        # Reusing it gets a 401 where the suite means to assert a 403, and the
        # failure reads as the permission check being wrong.
        viewer = session()
        viewer["base"] = BASE
        st, vd = rq(viewer, "POST", "/api/auth/login",
                    {"email": victim_email, "password": final_pw})
        ck("the Viewer can sign in with the password the reset set", st == 200,
           str(st))
        viewer["t"] = (vd or {}).get("csrf_token")
        st, _ = rq(viewer, "GET", "/api/email/test")
        ck("a Viewer cannot read the status", st == 403, str(st))
        st, _ = rq(viewer, "POST", "/api/email/test", {})
        ck("nor send a test", st == 403, str(st))

    finally:
        print("-- restoring every setting this suite changed --")
        st, _ = rq(boss, "PUT", "/api/preferences", {"values": restore})
        ck("the restore saves", st == 200, str(st))
        st, prefs = rq(boss, "GET", "/api/preferences")
        now = {p["key"]: p["value"] for p in prefs}
        drifted = [k for k in EMAIL_KEYS if (now.get(k) or "") != restore[k]]
        ck("and every email setting is back as it was", not drifted,
           "; ".join(f"{k}: {restore[k]!r} -> {now.get(k)!r}" for k in drifted))

    return report(PASSED, FAILED)


if __name__ == "__main__":
    sys.exit(main())
